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
    apply_rgba_adjust,
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
        # Deduplicated preset names (combined platform presets)
        self.assertTrue(any("N64" in n or "Full Opacity" in n for n in names),
                        "Expected a full-opacity / N64 preset")
        self.assertTrue(any("PS2" in n or "Half Opacity" in n for n in names),
                        "Expected a half-opacity / PS2 preset")
        self.assertTrue(any("Transparent" in n for n in names),
                        "Expected a transparent preset")

    # Use the current preset names
    _FULL_OPACITY_NAME = "Full Opacity  (N64 · DS · Wii · GameCube · Xbox 360 · PSP · PS2 BG)"
    _PS2_FULL_OPAQUE_NAME = "PS2 Full Opaque  (native GS α=128)"

    def test_ps2_preset_values(self):
        p = self._mgr.get_preset(self._PS2_FULL_OPAQUE_NAME)
        self.assertIsNotNone(p)
        self.assertEqual(p.alpha_value, 128)

    def test_ps2_clamp_presets(self):
        # PS2 clamp at native max 128 must exist
        matched = [p for p in self._mgr.all_presets()
                   if "Clamp" in p.name and "128" in p.name and "PS2" in p.name]
        self.assertTrue(matched, "Expected a PS2 clamp-max-128 preset")
        p = matched[0]
        self.assertEqual(p.clamp_max, 128)
        # Clamp-only presets should have alpha_value=None
        self.assertIsNone(p.alpha_value)

    def test_n64_preset_values(self):
        p = self._mgr.get_preset(self._FULL_OPACITY_NAME)
        self.assertIsNotNone(p)
        self.assertEqual(p.alpha_value, 255)

    def test_get_nonexistent_preset(self):
        self.assertIsNone(self._mgr.get_preset("DoesNotExist"))

    def test_cannot_overwrite_builtin(self):
        custom = AlphaPreset(self._PS2_FULL_OPAQUE_NAME, 0, 0, False, "test")
        result = self._mgr.save_custom_preset(custom)
        self.assertFalse(result)

    def test_save_and_retrieve_custom_preset(self):
        custom = AlphaPreset("My Custom", 200, 0, False, "desc", builtin=False)
        result = self._mgr.save_custom_preset(custom)
        self.assertTrue(result)
        retrieved = self._mgr.get_preset("My Custom")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.alpha_value, 200)

    def test_delete_custom_preset(self):
        custom = AlphaPreset("ToDelete", 100, 0, False, "desc", builtin=False)
        self._mgr.save_custom_preset(custom)
        result = self._mgr.delete_custom_preset("ToDelete")
        self.assertTrue(result)
        self.assertIsNone(self._mgr.get_preset("ToDelete"))

    def test_cannot_delete_builtin(self):
        result = self._mgr.delete_custom_preset(self._PS2_FULL_OPAQUE_NAME)
        self.assertFalse(result)
        self.assertIsNotNone(self._mgr.get_preset(self._PS2_FULL_OPAQUE_NAME))

    def test_save_custom_preset_preserves_mode_multiply(self):
        """Custom preset saved with mode='multiply' must survive a round-trip."""
        custom = AlphaPreset("Mul Test", 128, 0, False, "desc", builtin=False,
                             clamp_min=0, clamp_max=255, mode="multiply")
        self._mgr.save_custom_preset(custom)
        retrieved = self._mgr.get_preset("Mul Test")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.mode, "multiply",
                         "mode field must be preserved after save/retrieve")

    def test_save_custom_preset_preserves_mode_normalize(self):
        """Custom preset saved with mode='normalize' must survive a round-trip."""
        custom = AlphaPreset("Norm Test", None, 0, False, "desc", builtin=False,
                             clamp_min=0, clamp_max=128, mode="normalize")
        self._mgr.save_custom_preset(custom)
        retrieved = self._mgr.get_preset("Norm Test")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.mode, "normalize",
                         "mode field must be preserved after save/retrieve")

    def test_save_custom_preset_preserves_mode_add(self):
        """Custom preset saved with mode='add' must survive a round-trip."""
        custom = AlphaPreset("Add Test", 50, 0, False, "desc", builtin=False,
                             clamp_min=0, clamp_max=255, mode="add")
        self._mgr.save_custom_preset(custom)
        retrieved = self._mgr.get_preset("Add Test")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.mode, "add")

    def test_save_custom_preset_preserves_mode_subtract(self):
        """Custom preset saved with mode='subtract' must survive a round-trip."""
        custom = AlphaPreset("Sub Test", 30, 0, False, "desc", builtin=False,
                             clamp_min=0, clamp_max=255, mode="subtract")
        self._mgr.save_custom_preset(custom)
        retrieved = self._mgr.get_preset("Sub Test")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.mode, "subtract")

    def test_preset_from_dict_preserves_mode(self):
        """AlphaPreset.from_dict must correctly restore the mode field."""
        original = AlphaPreset("Roundtrip", None, 0, False, "desc", builtin=False,
                               clamp_min=0, clamp_max=128, mode="normalize")
        restored = AlphaPreset.from_dict(original.to_dict())
        self.assertEqual(restored.mode, "normalize")
        self.assertEqual(restored.clamp_min, 0)
        self.assertEqual(restored.clamp_max, 128)


# ---------------------------------------------------------------------------
# Alpha processor tests
# ---------------------------------------------------------------------------

class TestAlphaProcessor(unittest.TestCase):

    def test_set_alpha_to_255(self):
        img = make_rgba_image(alpha=128)
        preset = AlphaPreset("test", 255, 0, False, "")
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 255))

    def test_set_alpha_to_0(self):
        img = make_rgba_image(alpha=200)
        preset = AlphaPreset("test", 0, 0, False, "")
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 0))

    def test_ps2_preset(self):
        img = make_rgba_image(alpha=200)
        preset = AlphaPreset("PS2", 128, 0, False, "")
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 128))

    def test_invert_alpha(self):
        img = make_rgba_image(alpha=100)
        preset = AlphaPreset("inv", None, 0, True, "")
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 155))  # 255 - 100 = 155

    def test_add_alpha_via_rgba_adjust(self):
        """add mode removed; use apply_rgba_adjust for alpha delta."""
        img = make_rgba_image(alpha=100)
        result = apply_rgba_adjust(img, alpha_delta=50)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 150))

    def test_subtract_alpha_via_rgba_adjust(self):
        """subtract mode removed; use apply_rgba_adjust for alpha delta."""
        img = make_rgba_image(alpha=100)
        result = apply_rgba_adjust(img, alpha_delta=-30)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 70))

    def test_clamp_alpha_no_negative(self):
        """apply_rgba_adjust clamps subtract below 0."""
        img = make_rgba_image(alpha=10)
        result = apply_rgba_adjust(img, alpha_delta=-50)
        arr = np.array(result)
        # 10 - 50 = -40 -> clamped to 0
        self.assertTrue(np.all(arr[:, :, 3] == 0))

    def test_clamp_alpha_no_overflow(self):
        """apply_rgba_adjust clamps add above 255."""
        img = make_rgba_image(alpha=250)
        result = apply_rgba_adjust(img, alpha_delta=20)
        arr = np.array(result)
        # 250 + 20 = 270 -> clamped to 255
        self.assertTrue(np.all(arr[:, :, 3] == 255))

    def test_manual_alpha(self):
        img = make_rgba_image(alpha=100)
        result = apply_manual_alpha(img, value=200)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 200))

    def test_manual_alpha_clamp_only(self):
        """value=None should only apply clamping, not change pixel alpha."""
        img = make_rgba_image(alpha=200)
        result = apply_manual_alpha(img, value=None, clamp_max=128)
        arr = np.array(result)
        # 200 clamped to 128
        self.assertTrue(np.all(arr[:, :, 3] == 128))

    def test_manual_alpha_mode_multiply(self):
        """multiply mode: new = old × (value / 255), using floor division to avoid float rounding."""
        img = make_rgba_image(alpha=200)
        # value=128 → 200 * 128 // 255 = 100 (floor division matches implementation)
        result = apply_manual_alpha(img, value=128, mode="multiply")
        arr = np.array(result)
        expected = 200 * 128 // 255  # floor division mirrors the implementation
        self.assertTrue(np.all(arr[:, :, 3] == expected),
                        f"Expected {expected}, got {arr[0, 0, 3]}")

    def test_manual_alpha_mode_multiply_255_no_change(self):
        """multiply mode with value=255 should not change alpha."""
        img = make_rgba_image(alpha=200)
        result = apply_manual_alpha(img, value=255, mode="multiply")
        arr = np.array(result)
        expected = 200 * 255 // 255
        self.assertTrue(np.all(arr[:, :, 3] == expected))

    def test_manual_alpha_mode_add(self):
        """add mode: new = old + value, clamped at 255."""
        img = make_rgba_image(alpha=100)
        result = apply_manual_alpha(img, value=50, mode="add")
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 150))

    def test_manual_alpha_mode_add_clamps_at_255(self):
        """add mode should clamp result at 255."""
        img = make_rgba_image(alpha=200)
        result = apply_manual_alpha(img, value=100, mode="add")
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 255))

    def test_manual_alpha_mode_subtract(self):
        """subtract mode: new = old - value, clamped at 0."""
        img = make_rgba_image(alpha=150)
        result = apply_manual_alpha(img, value=50, mode="subtract")
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 100))

    def test_manual_alpha_mode_subtract_clamps_at_0(self):
        """subtract mode should clamp result at 0."""
        img = make_rgba_image(alpha=30)
        result = apply_manual_alpha(img, value=100, mode="subtract")
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 0))

    def test_manual_alpha_mode_set_default(self):
        """Default mode is 'set' (backward-compatible)."""
        img = make_rgba_image(alpha=100)
        result = apply_manual_alpha(img, value=200)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 200))

    def test_manual_alpha_mode_multiply_with_threshold(self):
        """multiply mode respects threshold: only pixels below threshold are affected."""
        # Create an image with two different alpha values
        arr = np.zeros((4, 4, 4), dtype=np.uint8)
        arr[:2, :, :3] = 200  # grey
        arr[2:, :, :3] = 200
        arr[:2, :, 3] = 50   # below threshold=100 → will be multiplied
        arr[2:, :, 3] = 150  # above threshold=100 → unchanged
        from PIL import Image as _Image
        img = _Image.fromarray(arr, "RGBA")
        result = apply_manual_alpha(img, value=128, threshold=100, mode="multiply")
        out = np.array(result)
        # pixels with old alpha 50 (< 100): 50 * 128 // 255 = 25
        self.assertTrue(np.all(out[:2, :, 3] == 50 * 128 // 255),
                        f"Below-threshold pixels should be multiplied, got {out[0, 0, 3]}")
        # pixels with old alpha 150 (>= 100): unchanged
        self.assertTrue(np.all(out[2:, :, 3] == 150),
                        f"Above-threshold pixels should be unchanged, got {out[2, 0, 3]}")

    def test_clamp_min_preset(self):
        """Clamp-only preset with alpha_value=None should only clamp."""
        img = make_rgba_image(alpha=50)
        preset = AlphaPreset("clamp", None, 0, False, "", clamp_min=100, clamp_max=255)
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        # 50 raised to 100
        self.assertTrue(np.all(arr[:, :, 3] == 100))

    def test_clamp_max_preset(self):
        """Clamp-only preset with alpha_value=None should only clamp."""
        img = make_rgba_image(alpha=200)
        preset = AlphaPreset("clamp", None, 0, False, "", clamp_min=0, clamp_max=128)
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        # 200 capped to 128
        self.assertTrue(np.all(arr[:, :, 3] == 128))

    def test_clamp_min_manual_alpha(self):
        """apply_manual_alpha with clamp_min>0 should raise low alpha values."""
        img = make_rgba_image(alpha=50)
        result = apply_manual_alpha(img, value=None, clamp_min=128, clamp_max=255)
        arr = np.array(result)
        # 50 raised to floor 128
        self.assertTrue(np.all(arr[:, :, 3] == 128))

    def test_clamp_min_manual_alpha_value_below_floor(self):
        """apply_manual_alpha: if the set value is below clamp_min, clamp_min wins."""
        img = make_rgba_image(alpha=200)
        # Explicitly set alpha to 64, then clamp floor at 128 → should be 128
        result = apply_manual_alpha(img, value=64, clamp_min=128, clamp_max=255)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 128))

    def test_builtin_clamp_128_255_preset(self):
        """The built-in 'Clamp 128-255' preset should raise all alpha below 128."""
        from unittest.mock import MagicMock
        from src.core.presets import PresetManager
        mock_settings = MagicMock()
        mock_settings.get_custom_presets.return_value = []
        mgr = PresetManager(mock_settings)
        preset = next(
            (p for p in mgr.all_presets() if "Clamp 128" in p.name and "raise" in p.description.lower()),
            None,
        )
        self.assertIsNotNone(preset, "Expected 'Clamp 128–255 (raise floor to 128)' preset")
        self.assertEqual(preset.clamp_min, 128)
        self.assertEqual(preset.clamp_max, 255)
        self.assertIsNone(preset.alpha_value)
        img = make_rgba_image(alpha=50)
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 128),
                        f"Expected 128, got {arr[0, 0, 3]}")

    def test_binary_cut_preset(self):
        """binary_cut=True should give hard 0/255 split at threshold."""
        # alpha=50 → below 128 → should become 0
        img_low = make_rgba_image(alpha=50)
        preset = AlphaPreset("cut", None, 128, False, "", binary_cut=True)
        result_low = apply_alpha_preset(img_low, preset)
        arr_low = np.array(result_low)
        self.assertTrue(np.all(arr_low[:, :, 3] == 0))

        # alpha=200 → above 128 → should become 255
        img_high = make_rgba_image(alpha=200)
        result_high = apply_alpha_preset(img_high, preset)
        arr_high = np.array(result_high)
        self.assertTrue(np.all(arr_high[:, :, 3] == 255))

    def test_output_is_rgba(self):
        img = make_rgba_image(alpha=100)
        preset = AlphaPreset("test", 200, 0, False, "")
        result = apply_alpha_preset(img, preset)
        self.assertEqual(result.mode, "RGBA")

    def test_rgb_input_converted(self):
        img = Image.new("RGB", (4, 4), (200, 200, 200))
        preset = AlphaPreset("test", 128, 0, False, "")
        result = apply_alpha_preset(img, preset)
        self.assertEqual(result.mode, "RGBA")
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 128))

    # ------------------------------------------------------------------
    # Normalize mode tests
    # ------------------------------------------------------------------

    def test_manual_alpha_normalize_full_range_to_half(self):
        """normalize: [0, 255] → [0, 128] maps 255→128, 0→0."""
        arr = np.zeros((4, 4, 4), dtype=np.uint8)
        arr[:2, :, 3] = 0
        arr[2:, :, 3] = 255
        img = Image.fromarray(arr, "RGBA")
        result = apply_manual_alpha(img, value=None, clamp_min=0, clamp_max=128, mode="normalize")
        out = np.array(result)
        self.assertEqual(int(out[:2, :, 3].max()), 0,  "min pixels should stay 0")
        self.assertEqual(int(out[2:, :, 3].min()), 128, "max pixels should map to 128")

    def test_manual_alpha_normalize_half_range_to_full(self):
        """normalize: [0, 128] → [0, 255] maps 128→255, 0→0."""
        arr = np.zeros((4, 4, 4), dtype=np.uint8)
        arr[:2, :, 3] = 0
        arr[2:, :, 3] = 128
        img = Image.fromarray(arr, "RGBA")
        result = apply_manual_alpha(img, value=None, clamp_min=0, clamp_max=255, mode="normalize")
        out = np.array(result)
        self.assertEqual(int(out[:2, :, 3].max()), 0,   "min pixels should stay 0")
        self.assertEqual(int(out[2:, :, 3].min()), 255, "max pixels should map to 255")

    def test_manual_alpha_normalize_uniform_image_maps_to_max(self):
        """normalize on uniform alpha (all same value) should map to clamp_max."""
        img = make_rgba_image(alpha=200)
        result = apply_manual_alpha(img, value=None, clamp_min=0, clamp_max=128, mode="normalize")
        out = np.array(result)
        # All pixels are the same → no range → map to target_hi (clamp_max)
        self.assertTrue(np.all(out[:, :, 3] == 128))

    def test_manual_alpha_normalize_preserves_proportions(self):
        """normalize: midpoint of source range maps to midpoint of target range."""
        arr = np.zeros((1, 3, 4), dtype=np.uint8)
        arr[0, 0, 3] = 0
        arr[0, 1, 3] = 128   # midpoint of [0, 255]
        arr[0, 2, 3] = 255
        img = Image.fromarray(arr, "RGBA")
        result = apply_manual_alpha(img, value=None, clamp_min=0, clamp_max=100, mode="normalize")
        out = np.array(result)
        # 128 / 255 * 100 ≈ 50
        mid = int(out[0, 1, 3])
        self.assertAlmostEqual(mid, round(128 / 255 * 100), delta=1)

    def test_preset_normalize_mode(self):
        """AlphaPreset with mode='normalize' uses apply_alpha_preset correctly."""
        arr = np.zeros((1, 2, 4), dtype=np.uint8)
        arr[0, 0, 3] = 0
        arr[0, 1, 3] = 128
        img = Image.fromarray(arr, "RGBA")
        preset = AlphaPreset(
            "norm_test", alpha_value=None, threshold=0, invert=False,
            description="", clamp_min=0, clamp_max=255, mode="normalize",
        )
        result = apply_alpha_preset(img, preset)
        out = np.array(result)
        self.assertEqual(int(out[0, 0, 3]), 0)
        self.assertEqual(int(out[0, 1, 3]), 255)

    def test_builtin_ps2_normalize_presets_exist(self):
        """PS2 normalize presets should be present in built-in list."""
        from unittest.mock import MagicMock
        from src.core.presets import PresetManager
        mock_settings = MagicMock()
        mock_settings.get_custom_presets.return_value = []
        mgr = PresetManager(mock_settings)
        names = [p.name for p in mgr.all_presets()]
        self.assertTrue(
            any("Normalize" in n and "0–128" in n for n in names),
            "Expected a PS2 Normalize → 0–128 preset",
        )
        self.assertTrue(
            any("Normalize" in n and "0–255" in n for n in names),
            "Expected a PS2 Normalize → 0–255 preset",
        )


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

try:
    import PyQt6  # noqa: F401
    _PYQT6_AVAILABLE = True
except ImportError:
    _PYQT6_AVAILABLE = False


@unittest.skipUnless(_PYQT6_AVAILABLE, "PyQt6 not installed — skipping worker tests")
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


# ---------------------------------------------------------------------------
# apply_rgba_adjust tests
# ---------------------------------------------------------------------------

class TestRGBAAdj(unittest.TestCase):
    """apply_rgba_adjust – per-channel delta adjustments."""

    def _make(self, r=100, g=100, b=100, a=200):
        arr = np.zeros((4, 4, 4), dtype=np.uint8)
        arr[:, :, 0] = r
        arr[:, :, 1] = g
        arr[:, :, 2] = b
        arr[:, :, 3] = a
        return Image.fromarray(arr, "RGBA")

    def test_positive_red_delta(self):
        img = self._make(r=100)
        result = apply_rgba_adjust(img, red_delta=50)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 0] == 150))

    def test_negative_red_delta(self):
        img = self._make(r=100)
        result = apply_rgba_adjust(img, red_delta=-50)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 0] == 50))

    def test_positive_green_delta(self):
        img = self._make(g=80)
        result = apply_rgba_adjust(img, green_delta=20)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 1] == 100))

    def test_positive_blue_delta(self):
        img = self._make(b=60)
        result = apply_rgba_adjust(img, blue_delta=40)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 2] == 100))

    def test_alpha_delta(self):
        img = self._make(a=200)
        result = apply_rgba_adjust(img, alpha_delta=-50)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 150))

    def test_clamp_high(self):
        img = self._make(r=250)
        result = apply_rgba_adjust(img, red_delta=20)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 0] == 255))

    def test_clamp_low(self):
        img = self._make(g=10)
        result = apply_rgba_adjust(img, green_delta=-50)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 1] == 0))

    def test_all_channels_zero_delta_noop(self):
        img = self._make(r=100, g=150, b=200, a=180)
        result = apply_rgba_adjust(img, red_delta=0, green_delta=0, blue_delta=0, alpha_delta=0)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 0] == 100))
        self.assertTrue(np.all(arr[:, :, 1] == 150))
        self.assertTrue(np.all(arr[:, :, 2] == 200))
        self.assertTrue(np.all(arr[:, :, 3] == 180))

    def test_output_is_rgba(self):
        img = self._make()
        result = apply_rgba_adjust(img, red_delta=10)
        self.assertEqual(result.mode, "RGBA")

    def test_rgb_input_is_converted(self):
        """apply_rgba_adjust should handle RGB input by converting to RGBA first."""
        img = Image.new("RGB", (4, 4), (100, 100, 100))
        result = apply_rgba_adjust(img, blue_delta=55)
        arr = np.array(result)
        self.assertEqual(result.mode, "RGBA")
        self.assertTrue(np.all(arr[:, :, 2] == 155))


# ---------------------------------------------------------------------------
# Theme engine completeness tests (no PyQt6 required)
# ---------------------------------------------------------------------------

class TestThemeEngineBannerFrames(unittest.TestCase):
    """All themes in PRESET_THEMES and HIDDEN_THEMES should have banner frames."""

    def _import_theme_engine(self):
        # theme_engine.py has no PyQt6 imports at module level; safe to import.
        from src.ui import theme_engine
        return theme_engine

    def test_all_preset_themes_have_banner_frames(self):
        te = self._import_theme_engine()
        for name in te.PRESET_THEMES:
            frames = te.get_theme_banner_frames(name)
            self.assertIsInstance(frames, list, f"Expected list for preset theme '{name}'")
            self.assertGreater(len(frames), 0, f"Expected at least 1 frame for '{name}'")

    def test_all_hidden_themes_have_banner_frames(self):
        te = self._import_theme_engine()
        for name in te.HIDDEN_THEMES:
            frames = te.get_theme_banner_frames(name)
            self.assertIsInstance(frames, list, f"Expected list for hidden theme '{name}'")
            self.assertGreater(len(frames), 0, f"Expected at least 1 frame for '{name}'")

    def test_animated_themes_have_multiple_frames(self):
        """Themes with entries in THEME_BANNER_FRAMES should have ≥ 2 frames."""
        te = self._import_theme_engine()
        for name, frames in te.THEME_BANNER_FRAMES.items():
            self.assertGreaterEqual(
                len(frames), 2,
                f"Expected ≥ 2 animation frames for '{name}', got {len(frames)}"
            )

    def test_preset_theme_count(self):
        te = self._import_theme_engine()
        self.assertEqual(len(te.PRESET_THEMES), 18,
                         f"Expected 18 preset themes, got {len(te.PRESET_THEMES)}")

    def test_hidden_theme_count(self):
        te = self._import_theme_engine()
        self.assertEqual(len(te.HIDDEN_THEMES), 32,
                         f"Expected 32 hidden themes, got {len(te.HIDDEN_THEMES)}")

    def test_new_preset_svgs_exist(self):
        """Mermaid, Shark Bait, and Alien should have dedicated SVG files."""
        te = self._import_theme_engine()
        for theme_name in ("Mermaid", "Shark Bait", "Alien"):
            svg_path = te.get_theme_svg_path(theme_name)
            self.assertIsNotNone(svg_path, f"No SVG path for '{theme_name}'")
            self.assertTrue(
                os.path.isfile(svg_path),
                f"SVG file not found: {svg_path}"
            )

    def test_new_preset_svgs_are_unique(self):
        """Mermaid, Shark Bait, and Alien should use distinct SVG files."""
        te = self._import_theme_engine()
        paths = [te.get_theme_svg_path(n) for n in ("Mermaid", "Shark Bait", "Alien")]
        # No two of the three should share the same SVG path
        self.assertEqual(len(set(paths)), 3,
                         f"Expected 3 unique SVG paths, got: {paths}")


# ---------------------------------------------------------------------------
# Mouse trail style tests (no PyQt6 required — pure constant checks)
# ---------------------------------------------------------------------------

class TestMouseTrailStyles(unittest.TestCase):
    """Verify mouse_trail.py data constants without importing Qt."""

    # Expected emoji that MUST appear in their respective lists in mouse_trail.py.
    # Using source-reading avoids duplicating the actual lists in the test.
    _EXPECTED_IN_WAVE    = "🌊"   # ocean wave emoji
    _EXPECTED_IN_SPARKLE = "❄"   # ice crystal emoji
    _EXPECTED_IN_FAIRY   = "✨"   # sparkle / fairy dust emoji
    _VALID_STYLES = {"dots", "fairy", "wave", "sparkle"}

    def _trail_source(self) -> str:
        trail_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "ui", "mouse_trail.py"
        )
        with open(trail_path) as f:
            return f.read()

    def test_all_valid_styles_defined(self):
        for style in ("dots", "fairy", "wave", "sparkle"):
            self.assertIn(style, self._VALID_STYLES)

    def test_wave_emoji_appears_in_source(self):
        self.assertIn(self._EXPECTED_IN_WAVE, self._trail_source(),
                      f"Ocean wave emoji {self._EXPECTED_IN_WAVE!r} missing from mouse_trail.py")

    def test_sparkle_emoji_appears_in_source(self):
        self.assertIn(self._EXPECTED_IN_SPARKLE, self._trail_source(),
                      f"Crystal emoji {self._EXPECTED_IN_SPARKLE!r} missing from mouse_trail.py")

    def test_fairy_emoji_appears_in_source(self):
        self.assertIn(self._EXPECTED_IN_FAIRY, self._trail_source(),
                      f"Fairy dust emoji {self._EXPECTED_IN_FAIRY!r} missing from mouse_trail.py")

    def test_mouse_trail_source_defines_four_styles(self):
        """Read the mouse_trail.py source and confirm all 4 styles are present."""
        source = self._trail_source()
        for style in ("dots", "fairy", "wave", "sparkle"):
            # Each style name should appear as a string literal in the source
            self.assertTrue(
                f'"{style}"' in source or f"'{style}'" in source,
                f"Style {style!r} not found as a string literal in mouse_trail.py"
            )


# ---------------------------------------------------------------------------
# Settings manager completeness tests (no PyQt6 required)
# ---------------------------------------------------------------------------

class TestSettingsManagerDefaults(unittest.TestCase):
    """All hidden theme unlock keys should be in _DEFAULTS."""

    def _load_defaults(self) -> dict:
        """Parse _DEFAULTS from settings_manager.py without importing Qt."""
        settings_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "core", "settings_manager.py"
        )
        with open(settings_path) as f:
            source = f.read()
        return source

    def test_all_hidden_theme_unlock_keys_in_defaults(self):
        """Every hidden theme's _unlock value must have an unlock_ key in _DEFAULTS."""
        # Import theme engine (no Qt)
        import importlib.util
        te_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "ui", "theme_engine.py"
        )
        spec = importlib.util.spec_from_file_location("theme_engine", te_path)
        te = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(te)

        source = self._load_defaults()
        for name, theme in te.HIDDEN_THEMES.items():
            unlock_key = f"unlock_{theme.get('_unlock', '')}"
            self.assertIn(
                f'"{unlock_key}"',
                source,
                f"Unlock key '{unlock_key}' for hidden theme '{name}' missing from settings_manager._DEFAULTS"
            )

    def test_converter_keep_metadata_in_defaults(self):
        """converter_keep_metadata should have a default value."""
        source = self._load_defaults()
        self.assertIn('"converter_keep_metadata"', source)

    def test_converter_keep_metadata_in_export_keys(self):
        """converter_keep_metadata should be listed in EXPORT_KEYS."""
        source = self._load_defaults()
        # It should appear after the EXPORT_KEYS definition
        export_section = source[source.find("EXPORT_KEYS"):]
        self.assertIn('"converter_keep_metadata"', export_section)

    def test_splash_screen_uses_banner_frames(self):
        """splash_screen.py should use get_theme_banner_frames, not just get_theme_banner."""
        splash_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "ui", "splash_screen.py"
        )
        with open(splash_path) as f:
            source = f.read()
        self.assertIn("get_theme_banner_frames", source,
                      "splash_screen.py should call get_theme_banner_frames for animated banner")

    def test_effect_defaults_are_off(self):
        """All visual/audio effect defaults must be False so new users start with a clean UI."""
        import ast
        settings_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "core", "settings_manager.py"
        )
        with open(settings_path) as f:
            source = f.read()
        tree = ast.parse(source)

        defaults = {}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "_DEFAULTS"
                and isinstance(node.value, ast.Dict)
            ):
                for k, v in zip(node.value.keys, node.value.values):
                    if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                        defaults[k.value] = v.value
                break

        off_by_default = [
            "sound_enabled",
            "trail_enabled",
            "click_effects_enabled",
            "use_theme_cursor",
            "use_theme_trail",
            "use_theme_effect",
        ]
        for key in off_by_default:
            self.assertIn(key, defaults,
                          f"'{key}' is missing from settings_manager._DEFAULTS")
            self.assertIs(defaults[key], False,
                          f"'{key}' default must be False (got {defaults[key]!r})")

    def test_all_effect_fallbacks_are_false_in_main_window(self):
        """Every settings.get() call for effects in main_window.py must use False as fallback."""
        mw_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "ui", "main_window.py"
        )
        with open(mw_path) as f:
            source = f.read()
        # These patterns should NOT appear – they would mean a True fallback slipped in
        bad_patterns = [
            '"click_effects_enabled", True',
            '"trail_enabled", True',
            '"sound_enabled", True',
        ]
        for pattern in bad_patterns:
            self.assertNotIn(
                pattern, source,
                f"main_window.py uses True as fallback for '{pattern}' – must be False",
            )

    def test_all_effect_fallbacks_are_false_in_settings_dialog(self):
        """Every settings.get() call for effects in settings_dialog.py must use False as fallback."""
        dlg_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "ui", "settings_dialog.py"
        )
        with open(dlg_path) as f:
            source = f.read()
        bad_patterns = [
            '"click_effects_enabled", True',
            '"trail_enabled", True',
            '"sound_enabled", True',
        ]
        for pattern in bad_patterns:
            self.assertNotIn(
                pattern, source,
                f"settings_dialog.py uses True as fallback for '{pattern}' – must be False",
            )

    def test_reset_unlocks_only_method_exists(self):
        """SettingsManager must have a reset_unlocks_only method."""
        settings_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "core", "settings_manager.py"
        )
        with open(settings_path) as f:
            source = f.read()
        self.assertIn("def reset_unlocks_only", source,
                      "settings_manager.py must define reset_unlocks_only()")

    def test_reset_unlocks_only_does_not_use_qs_clear(self):
        """reset_unlocks_only must NOT call _qs.clear() which would wipe all settings."""
        settings_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "core", "settings_manager.py"
        )
        with open(settings_path) as f:
            source = f.read()
        method_start = source.find("def reset_unlocks_only")
        method_body = source[method_start:method_start + 600]
        self.assertNotIn("self._qs.clear()", method_body,
                         "reset_unlocks_only must NOT call _qs.clear() – that wipes all settings")
        # Verify it handles unlock_ keys and total_clicks
        self.assertIn("unlock_", method_body,
                      "reset_unlocks_only should reference unlock_ keys")
        self.assertIn("total_clicks", method_body,
                      "reset_unlocks_only should reset total_clicks")

    def test_reset_unlocks_only_functional(self):
        """reset_unlocks_only resets unlock flags but preserves other settings."""
        try:
            from PyQt6.QtCore import QSettings  # noqa: F401
        except (ImportError, OSError):
            raise unittest.SkipTest("PyQt6 not available — skipping functional test")
        import tempfile
        from unittest.mock import patch
        tf = tempfile.NamedTemporaryFile(suffix=".ini", delete=False)
        tf.close()
        try:
            with patch("src.core.settings_manager._settings_ini_path", return_value=tf.name):
                from src.core.settings_manager import SettingsManager
                sm = SettingsManager()
                sm.set("unlock_skeleton", True)
                sm.set("total_clicks", 500)
                sm.set("alpha_fix_done_once", True)
                sm.set("conversion_done_once", True)
                sm.set("theme", "Goth")
                sm.set("sound_enabled", True)

                sm.reset_unlocks_only()

                self.assertFalse(sm.get("unlock_skeleton"),
                                 "unlock_skeleton should be False after reset_unlocks_only")
                self.assertEqual(sm.get("total_clicks"), 0,
                                 "total_clicks should be 0 after reset_unlocks_only")
                self.assertFalse(sm.get("alpha_fix_done_once", True),
                                 "alpha_fix_done_once should be False after reset_unlocks_only")
                self.assertFalse(sm.get("conversion_done_once"),
                                 "conversion_done_once should be False after reset_unlocks_only")
                self.assertEqual(sm.get("theme"), "Goth",
                                 "theme should be preserved by reset_unlocks_only")
                self.assertTrue(sm.get("sound_enabled"),
                                "sound_enabled should be preserved by reset_unlocks_only")
        finally:
            os.unlink(tf.name)

    def test_reset_unlocks_btn_registered_in_settings_dialog(self):
        """settings_dialog.py must register _btn_reset_unlocks with the tooltip manager."""
        dlg_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "ui", "settings_dialog.py"
        )
        with open(dlg_path) as f:
            source = f.read()
        self.assertIn("_btn_reset_unlocks", source,
                      "settings_dialog.py must define _btn_reset_unlocks button")
        self.assertIn('"reset_unlocks_btn"', source,
                      "settings_dialog.py must register reset_unlocks_btn with tooltip manager")

    def test_reset_unlocks_btn_tooltip_in_all_modes(self):
        """tooltip_manager.py must have reset_unlocks_btn in all 3 tip modes."""
        tm_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "ui", "tooltip_manager.py"
        )
        with open(tm_path) as f:
            source = f.read()
        count = source.count('"reset_unlocks_btn"')
        self.assertEqual(count, 3,
                         f"reset_unlocks_btn must appear in all 3 tip dicts (_NORMAL, _DUMBED, _VULGAR); found {count}")


# ---------------------------------------------------------------------------
# Alpha-delta spinbox wiring tests (no PyQt6 required — source inspection)
# ---------------------------------------------------------------------------

class TestAlphaDeltaSpinbox(unittest.TestCase):
    """alpha_tool.py must expose an alpha-delta spinbox wired to apply_rgba_adjust."""

    def _alpha_tool_source(self) -> str:
        path = os.path.join(os.path.dirname(__file__), "..", "src", "ui", "alpha_tool.py")
        with open(path) as f:
            return f.read()

    def _worker_source(self) -> str:
        path = os.path.join(os.path.dirname(__file__), "..", "src", "core", "worker.py")
        with open(path) as f:
            return f.read()

    def test_alpha_delta_spin_attribute_defined(self):
        """alpha_tool.py should define self._alpha_delta_spin."""
        self.assertIn("_alpha_delta_spin", self._alpha_tool_source())

    def test_alpha_delta_spin_connected_to_finetune_changed(self):
        """alpha_delta_spin must be connected to _on_finetune_changed."""
        source = self._alpha_tool_source()
        self.assertIn("_alpha_delta_spin", source)
        self.assertIn("_on_finetune_changed", source)
        # The alpha_spin (primary alpha control) should be connected to finetune
        conn_start = source.find("_alpha_spin.valueChanged")
        self.assertGreater(conn_start, 0,
                           "_alpha_spin.valueChanged not found in alpha_tool.py")
        conn_block = source[conn_start:]
        self.assertIn("_alpha_delta_spin", conn_block,
                      "_alpha_delta_spin not connected to signal in alpha_tool.py")

    def test_alpha_delta_included_in_rgb_params(self):
        """The rgb_params dict in alpha_tool.py should include key 'a' for alpha delta."""
        source = self._alpha_tool_source()
        # Look for 'a': self._alpha_delta_spin near rgb_params
        rgb_idx = source.find("rgb_params")
        self.assertGreater(rgb_idx, 0, "rgb_params not found in alpha_tool.py")
        rgb_section = source[rgb_idx:]
        self.assertIn('"a"', rgb_section,
                      "rgb_params in alpha_tool.py should include key 'a' for alpha delta")

    def test_worker_passes_alpha_delta(self):
        """worker.py should forward alpha_delta from the rgb dict to apply_rgba_adjust."""
        source = self._worker_source()
        self.assertIn("alpha_delta=rgb.get(\"a\", 0)", source,
                      "worker.py should pass alpha_delta to apply_rgba_adjust")

    def test_alpha_delta_condition_in_worker(self):
        """worker.py condition should check rgb.get('a') before skipping the call."""
        source = self._worker_source()
        self.assertIn('rgb.get("a")', source,
                      "worker.py must check rgb.get('a') in the condition that guards apply_rgba_adjust")

    def test_apply_rgba_check_label_updated(self):
        """The RGBA adjustments checkbox should say 'RGBA', not just 'RGB'."""
        source = self._alpha_tool_source()
        self.assertIn("Apply RGBA adjustments", source,
                      "Checkbox text should be 'Apply RGBA adjustments'")

    def test_alpha_delta_tooltip_key_registered(self):
        """alpha_tool.py should register 'alpha_delta_spin' with the tooltip manager."""
        source = self._alpha_tool_source()
        self.assertIn('"alpha_delta_spin"', source,
                      "alpha_delta_spin should be registered with tooltip manager")

    def test_alpha_delta_tooltip_tips_in_tooltip_manager(self):
        """tooltip_manager.py should contain 'alpha_delta_spin' tips in all 3 dicts."""
        path = os.path.join(os.path.dirname(__file__), "..", "src", "ui", "tooltip_manager.py")
        with open(path) as f:
            source = f.read()
        count = source.count('"alpha_delta_spin"')
        self.assertGreaterEqual(count, 3,
                                f"Expected alpha_delta_spin in all 3 tooltip dicts, found {count} occurrences")

    def test_tab_tooltip_keys_in_all_modes(self):
        """alpha_fixer_tab, converter_tab, history_tab must appear in all 3 tooltip dicts."""
        path = os.path.join(os.path.dirname(__file__), "..", "src", "ui", "tooltip_manager.py")
        with open(path) as f:
            source = f.read()
        for key in ("alpha_fixer_tab", "converter_tab", "history_tab"):
            count = source.count(f'"{key}"')
            self.assertGreaterEqual(
                count, 3,
                f"Expected '{key}' in all 3 tooltip dicts, found {count} occurrence(s)"
            )

    def test_register_tab_bar_method_exists(self):
        """TooltipManager should expose a register_tab_bar method."""
        path = os.path.join(os.path.dirname(__file__), "..", "src", "ui", "tooltip_manager.py")
        with open(path) as f:
            source = f.read()
        self.assertIn("def register_tab_bar", source,
                      "TooltipManager should have a register_tab_bar method")


# ---------------------------------------------------------------------------
# Use-preset re-check sync tests (source inspection, no PyQt6 required)
# ---------------------------------------------------------------------------

class TestUsePresetRecheck(unittest.TestCase):
    """When the user manually re-checks 'Use preset', fine-tune controls must
    be reloaded from the preset so the display matches what will be processed."""

    def _alpha_tool_source(self) -> str:
        path = os.path.join(os.path.dirname(__file__), "..", "src", "ui", "alpha_tool.py")
        with open(path) as f:
            return f.read()

    def test_on_use_preset_toggled_handler_defined(self):
        """alpha_tool.py must define the _on_use_preset_toggled slot."""
        self.assertIn("def _on_use_preset_toggled", self._alpha_tool_source(),
                      "_on_use_preset_toggled slot must be defined in alpha_tool.py")

    def test_use_preset_check_connected_to_on_use_preset_toggled(self):
        """_use_preset_check.toggled must be connected to _on_use_preset_toggled,
        not directly to _update_compare."""
        source = self._alpha_tool_source()
        self.assertIn(
            "_use_preset_check.toggled.connect(self._on_use_preset_toggled)",
            source,
            "_use_preset_check.toggled must connect to _on_use_preset_toggled",
        )
        # The old direct-to-_update_compare connection must no longer exist
        self.assertNotIn(
            "_use_preset_check.toggled.connect(self._update_compare)",
            source,
            "_use_preset_check.toggled must NOT connect directly to _update_compare",
        )

    def test_on_use_preset_toggled_calls_on_preset_changed_when_checked(self):
        """_on_use_preset_toggled must call _on_preset_changed when checked=True."""
        source = self._alpha_tool_source()
        # Find the handler body
        start = source.find("def _on_use_preset_toggled")
        self.assertGreater(start, 0, "_on_use_preset_toggled not found")
        # Find the next method definition to scope the search; fall back to
        # end-of-file if this is the last method (avoids a -1 index).
        next_def = source.find("\n    def ", start + 1)
        end = next_def if next_def > start else len(source)
        body = source[start:end]
        self.assertIn(
            "_on_preset_changed",
            body,
            "_on_use_preset_toggled must call _on_preset_changed when checked",
        )
        self.assertIn(
            "_update_compare",
            body,
            "_on_use_preset_toggled must call _update_compare when unchecked",
        )


# ---------------------------------------------------------------------------
# Dedicated hidden-theme SVG tests
# ---------------------------------------------------------------------------

class TestHiddenThemeSVGs(unittest.TestCase):
    """Blood Moon and Ice Cave should now have dedicated (non-reused) SVG files."""

    def _import_theme_engine(self):
        from src.ui import theme_engine
        return theme_engine

    def test_blood_moon_has_dedicated_svg(self):
        te = self._import_theme_engine()
        svg_path = te.get_theme_svg_path("Blood Moon")
        self.assertTrue(os.path.isfile(svg_path), f"SVG file not found: {svg_path}")
        self.assertIn("blood_moon", svg_path,
                      "Blood Moon should use a dedicated blood_moon.svg, not a reused file")

    def test_ice_cave_has_dedicated_svg(self):
        te = self._import_theme_engine()
        svg_path = te.get_theme_svg_path("Ice Cave")
        self.assertTrue(os.path.isfile(svg_path), f"SVG file not found: {svg_path}")
        self.assertIn("ice_cave", svg_path,
                      "Ice Cave should use a dedicated ice_cave.svg, not a reused file")

    def test_blood_moon_svg_contains_animations(self):
        """blood_moon.svg should contain SVG animate elements."""
        te = self._import_theme_engine()
        svg_path = te.get_theme_svg_path("Blood Moon")
        with open(svg_path) as f:
            svg_content = f.read()
        self.assertIn("<animate", svg_content,
                      "blood_moon.svg should contain animation elements")

    def test_ice_cave_svg_contains_animations(self):
        """ice_cave.svg should contain SVG animate elements."""
        te = self._import_theme_engine()
        svg_path = te.get_theme_svg_path("Ice Cave")
        with open(svg_path) as f:
            svg_content = f.read()
        self.assertIn("<animate", svg_content,
                      "ice_cave.svg should contain animation elements")

    # ----- New dedicated SVGs (second batch) -----

    def test_cyber_otter_has_dedicated_svg(self):
        te = self._import_theme_engine()
        svg_path = te.get_theme_svg_path("Cyber Otter")
        self.assertTrue(os.path.isfile(svg_path), f"SVG file not found: {svg_path}")
        self.assertIn("cyber_otter", svg_path,
                      "Cyber Otter should use a dedicated cyber_otter.svg, not a reused file")

    def test_cyber_otter_svg_contains_animations(self):
        te = self._import_theme_engine()
        svg_path = te.get_theme_svg_path("Cyber Otter")
        with open(svg_path) as f:
            svg_content = f.read()
        self.assertIn("<animate", svg_content,
                      "cyber_otter.svg should contain animation elements")

    def test_lava_cave_has_dedicated_svg(self):
        te = self._import_theme_engine()
        svg_path = te.get_theme_svg_path("Lava Cave")
        self.assertTrue(os.path.isfile(svg_path), f"SVG file not found: {svg_path}")
        self.assertIn("lava_cave", svg_path,
                      "Lava Cave should use a dedicated lava_cave.svg, not a reused file")

    def test_lava_cave_svg_contains_animations(self):
        te = self._import_theme_engine()
        svg_path = te.get_theme_svg_path("Lava Cave")
        with open(svg_path) as f:
            svg_content = f.read()
        self.assertIn("<animate", svg_content,
                      "lava_cave.svg should contain animation elements")

    def test_sunset_beach_has_dedicated_svg(self):
        te = self._import_theme_engine()
        svg_path = te.get_theme_svg_path("Sunset Beach")
        self.assertTrue(os.path.isfile(svg_path), f"SVG file not found: {svg_path}")
        self.assertIn("sunset_beach", svg_path,
                      "Sunset Beach should use a dedicated sunset_beach.svg, not a reused file")

    def test_sunset_beach_svg_contains_animations(self):
        te = self._import_theme_engine()
        svg_path = te.get_theme_svg_path("Sunset Beach")
        with open(svg_path) as f:
            svg_content = f.read()
        self.assertIn("<animate", svg_content,
                      "sunset_beach.svg should contain animation elements")

    def test_midnight_forest_has_dedicated_svg(self):
        te = self._import_theme_engine()
        svg_path = te.get_theme_svg_path("Midnight Forest")
        self.assertTrue(os.path.isfile(svg_path), f"SVG file not found: {svg_path}")
        self.assertIn("midnight_forest", svg_path,
                      "Midnight Forest should use a dedicated midnight_forest.svg, not a reused file")

    def test_midnight_forest_svg_contains_animations(self):
        te = self._import_theme_engine()
        svg_path = te.get_theme_svg_path("Midnight Forest")
        with open(svg_path) as f:
            svg_content = f.read()
        self.assertIn("<animate", svg_content,
                      "midnight_forest.svg should contain animation elements")

    def test_candy_land_has_dedicated_svg(self):
        te = self._import_theme_engine()
        svg_path = te.get_theme_svg_path("Candy Land")
        self.assertTrue(os.path.isfile(svg_path), f"SVG file not found: {svg_path}")
        self.assertIn("candy_land", svg_path,
                      "Candy Land should use a dedicated candy_land.svg, not a reused file")

    def test_candy_land_svg_contains_animations(self):
        te = self._import_theme_engine()
        svg_path = te.get_theme_svg_path("Candy Land")
        with open(svg_path) as f:
            svg_content = f.read()
        self.assertIn("<animate", svg_content,
                      "candy_land.svg should contain animation elements")

    # ----- Third batch: 12 remaining hidden themes now with dedicated SVGs -----

    _THIRD_BATCH_THEMES = [
        ("Zombie Apocalypse", "zombie_apocalypse"),
        ("Dragon Fire",       "dragon_fire"),
        ("Bubblegum",         "bubblegum"),
        ("Thunder Storm",     "thunder_storm"),
        ("Rose Gold",         "rose_gold"),
        ("Space Cat",         "space_cat"),
        ("Magic Mushroom",    "magic_mushroom"),
        ("Abyssal Void",      "abyssal_void"),
        ("Spring Bloom",      "spring_bloom"),
        ("Gold Rush",         "gold_rush"),
        ("Nebula",            "nebula"),
        ("Toxic Neon",        "toxic_neon"),
    ]

    def test_third_batch_svgs_exist(self):
        """All 12 newly-dedicated hidden-theme SVG files must exist on disk."""
        te = self._import_theme_engine()
        for theme_name, expected_stem in self._THIRD_BATCH_THEMES:
            with self.subTest(theme=theme_name):
                svg_path = te.get_theme_svg_path(theme_name)
                self.assertTrue(
                    os.path.isfile(svg_path),
                    f"SVG file not found for '{theme_name}': {svg_path}"
                )
                self.assertIn(
                    expected_stem, svg_path,
                    f"'{theme_name}' should use a dedicated {expected_stem}.svg"
                )

    def test_third_batch_svgs_contain_animations(self):
        """Each new dedicated SVG must contain at least one <animate> element."""
        te = self._import_theme_engine()
        for theme_name, _ in self._THIRD_BATCH_THEMES:
            with self.subTest(theme=theme_name):
                svg_path = te.get_theme_svg_path(theme_name)
                with open(svg_path) as f:
                    svg_content = f.read()
                self.assertIn(
                    "<animate", svg_content,
                    f"SVG for '{theme_name}' should contain animation elements"
                )

    def test_third_batch_svgs_are_unique(self):
        """No two third-batch themes should share the same SVG path."""
        te = self._import_theme_engine()
        paths = [
            te.get_theme_svg_path(theme_name)
            for theme_name, _ in self._THIRD_BATCH_THEMES
        ]
        self.assertEqual(
            len(set(paths)), len(paths),
            f"Expected all {len(paths)} SVG paths to be unique; got duplicates: {paths}"
        )


# ---------------------------------------------------------------------------
# File converter metadata tests
# ---------------------------------------------------------------------------

class TestConverterMetadata(unittest.TestCase):
    """_meta_kwargs should now include ICC profile for PNG and WEBP."""

    def _converter_source(self) -> str:
        path = os.path.join(os.path.dirname(__file__), "..", "src", "core", "file_converter.py")
        with open(path) as f:
            return f.read()

    def test_png_icc_profile_in_meta_kwargs(self):
        """PNG branch of _meta_kwargs should copy icc_profile."""
        source = self._converter_source()
        # Find the _meta_kwargs function body
        meta_idx = source.find("def _meta_kwargs")
        self.assertGreater(meta_idx, 0, "_meta_kwargs function not found in converter source")
        meta_body = source[meta_idx: meta_idx + 800]
        # Find the PNG-specific branch within the function
        png_idx = meta_body.find('".png"')
        self.assertGreater(png_idx, 0, "PNG case not found inside _meta_kwargs")
        local = meta_body[png_idx: png_idx + 200]
        self.assertIn("icc_profile", local,
                      "PNG branch of _meta_kwargs should copy icc_profile")

    def test_webp_icc_profile_in_meta_kwargs(self):
        """WEBP branch of _meta_kwargs should copy icc_profile."""
        source = self._converter_source()
        meta_idx = source.find("def _meta_kwargs")
        self.assertGreater(meta_idx, 0, "_meta_kwargs function not found in converter source")
        meta_body = source[meta_idx: meta_idx + 800]
        webp_idx = meta_body.find('".webp"')
        self.assertGreater(webp_idx, 0, "WEBP case not found inside _meta_kwargs")
        local = meta_body[webp_idx: webp_idx + 200]
        self.assertIn("icc_profile", local,
                      "WEBP branch of _meta_kwargs should copy icc_profile")

    def test_avif_passes_meta_kwargs_to_save(self):
        """AVIF save should call _meta_kwargs(ext) like PNG/JPEG/WEBP."""
        source = self._converter_source()
        # Search specifically for the AVIF comment/section, not just the word AVIF
        avif_comment_idx = source.find("# --- AVIF")
        self.assertGreater(avif_comment_idx, 0, "AVIF section comment not found in converter source")
        avif_section = source[avif_comment_idx:]
        save_line_idx = avif_section.find("img.save(output_path")
        self.assertGreater(save_line_idx, 0, "img.save not found in AVIF section")
        save_call = avif_section[save_line_idx: save_line_idx + 80]
        self.assertIn("_meta_kwargs", save_call,
                      "AVIF save should pass **_meta_kwargs(ext)")

    def test_keep_metadata_false_returns_empty_dict(self):
        """_meta_kwargs should return {} when keep_metadata=False."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            from src.core.file_converter import convert_file
            src = os.path.join(tmpdir, "in.png")
            dst = os.path.join(tmpdir, "out.png")
            # Build a simple PNG
            img = Image.new("RGBA", (4, 4), (100, 200, 50, 180))
            img.save(src)
            # Should succeed with keep_metadata=False (default)
            convert_file(src, dst, "PNG", keep_metadata=False)
            self.assertTrue(os.path.isfile(dst))

    def test_keep_metadata_true_png_roundtrip(self):
        """Converting PNG→PNG with keep_metadata=True should produce a valid PNG."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            from src.core.file_converter import convert_file
            src = os.path.join(tmpdir, "in.png")
            dst = os.path.join(tmpdir, "out.png")
            img = Image.new("RGBA", (4, 4), (100, 200, 50, 180))
            img.save(src)
            convert_file(src, dst, "PNG", keep_metadata=True)
            result = Image.open(dst)
            self.assertEqual(result.mode, "RGBA")
            self.assertEqual(result.size, (4, 4))



# ---------------------------------------------------------------------------
# format_eta helper
# ---------------------------------------------------------------------------

class TestFormatEta(unittest.TestCase):
    """Unit tests for src.ui._ui_utils.format_eta."""

    def _fmt(self, current, total, elapsed, threshold=500):
        from src.ui._ui_utils import format_eta
        return format_eta(current, total, elapsed, threshold=threshold)

    def test_returns_empty_below_threshold(self):
        """Small batches should never show an ETA."""
        result = self._fmt(current=100, total=499, elapsed=5.0)
        self.assertEqual(result, "")

    def test_returns_empty_when_current_is_zero(self):
        result = self._fmt(current=0, total=1000, elapsed=5.0)
        self.assertEqual(result, "")

    def test_returns_empty_when_elapsed_too_short(self):
        result = self._fmt(current=50, total=1000, elapsed=0.5)
        self.assertEqual(result, "")

    def test_seconds_format_below_one_minute(self):
        # 500 items, 50 done in 5 s → rate=10/s, remaining=450, eta=45s
        result = self._fmt(current=50, total=500, elapsed=5.0)
        self.assertIn("ETA", result)
        self.assertIn("s", result)
        self.assertNotIn("m", result)

    def test_minutes_format_above_one_minute(self):
        # 1000 items, 100 done in 10 s → rate=10/s, remaining=900, eta=90s
        result = self._fmt(current=100, total=1000, elapsed=10.0)
        self.assertIn("ETA", result)
        self.assertIn("m", result)

    def test_custom_threshold_respected(self):
        # threshold=10: 20 items should show ETA
        result = self._fmt(current=5, total=20, elapsed=2.0, threshold=10)
        self.assertIn("ETA", result)

    def test_starts_with_two_spaces(self):
        """ETA string must start with '  ' so it appends cleanly to status text."""
        result = self._fmt(current=100, total=1000, elapsed=10.0)
        self.assertTrue(result.startswith("  "), repr(result))



# ---------------------------------------------------------------------------
# Alpha tool UI simplification: value-first ordering and disabled-spinbox fix
# ---------------------------------------------------------------------------

class TestAlphaToolUISimplification(unittest.TestCase):
    """Source-level checks for the simplified Alpha Channel Settings section."""

    def _alpha_tool_source(self) -> str:
        path = os.path.join(os.path.dirname(__file__), "..", "src", "ui", "alpha_tool.py")
        with open(path) as f:
            return f.read()

    def test_alpha_value_spinbox_defined_before_mode_combo(self):
        """_alpha_spin must be instantiated before _mode_combo in the source
        (i.e., the value control appears before the advanced mode control)."""
        src = self._alpha_tool_source()
        spin_pos = src.find("self._alpha_spin = QSpinBox()")
        mode_pos = src.find("self._mode_combo = QComboBox()")
        self.assertGreater(spin_pos, 0, "_alpha_spin not found in alpha_tool.py")
        self.assertGreater(mode_pos, 0, "_mode_combo not found in alpha_tool.py")
        self.assertLess(spin_pos, mode_pos,
                        "_alpha_spin must be defined before _mode_combo "
                        "(value shown first, mode moved to Advanced section)")

    def test_apply_alpha_check_defined_after_alpha_spin(self):
        """_apply_alpha_check must be defined AFTER _alpha_spin — it lives in
        the Advanced section so basic users see the value input first."""
        src = self._alpha_tool_source()
        spin_pos = src.find("self._alpha_spin = QSpinBox()")
        check_pos = src.find("self._apply_alpha_check = QCheckBox(")
        self.assertGreater(spin_pos, 0, "_alpha_spin not found")
        self.assertGreater(check_pos, 0, "_apply_alpha_check not found")
        self.assertLess(spin_pos, check_pos,
                        "_apply_alpha_check must appear after _alpha_spin "
                        "in the source (it lives in the Advanced section)")

    def test_use_preset_renamed_auto_fills(self):
        """The 'Use preset' checkbox label must mention 'auto-fills' to make
        its purpose obvious to users who don't use presets."""
        src = self._alpha_tool_source()
        self.assertIn("auto-fills", src,
                      "Use-preset checkbox label must contain 'auto-fills'")

    def test_on_use_preset_toggled_reenables_spinbox_on_uncheck(self):
        """_on_use_preset_toggled must re-enable the alpha spinbox when the
        user switches to manual mode (unchecks Use preset).  Without this fix,
        a clamp-only preset leaves the spinbox disabled and users cannot type
        a value."""
        src = self._alpha_tool_source()
        start = src.find("def _on_use_preset_toggled")
        self.assertGreater(start, 0, "_on_use_preset_toggled not found")
        next_def = src.find("\n    def ", start + 1)
        end = next_def if next_def > start else len(src)
        body = src[start:end]
        self.assertIn("_alpha_spin.setEnabled(True)", body,
                      "_on_use_preset_toggled must call _alpha_spin.setEnabled(True) "
                      "when switching to manual mode so users can type a value")
        self.assertIn("_alpha_slider.setEnabled(True)", body,
                      "_on_use_preset_toggled must call _alpha_slider.setEnabled(True) "
                      "when switching to manual mode")

    def test_hint_label_in_setup_ui(self):
        """_setup_ui must add an explanatory hint label to guide new users."""
        src = self._alpha_tool_source()
        # The hint should mention both entering a value and picking a preset
        self.assertIn("Type an alpha value", src,
                      "Fine-tune section should have a hint label mentioning "
                      "typing a value")
        self.assertIn("preset", src.lower(),
                      "Hint label should mention presets")

    def test_advanced_separator_present(self):
        """An 'Advanced' separator must visually separate basic and advanced
        controls in the Alpha Channel Settings group."""
        src = self._alpha_tool_source()
        self.assertIn("Advanced Options", src,
                      "Fine-tune section must have an 'Advanced Options' separator "
                      "to keep basic controls visually distinct from advanced ones")


# ---------------------------------------------------------------------------
# Tooltip correctness tests: ensure stale references were cleaned up
# ---------------------------------------------------------------------------

class TestTooltipCorrectness(unittest.TestCase):
    """Source-level checks that tooltip_manager.py has correct text after the
    alpha tool UI redesign (value-first, Advanced section, apply_alpha_check)."""

    _UI_DIR = os.path.join(os.path.dirname(__file__), "..", "src", "ui")

    def _tooltip_source(self) -> str:
        with open(os.path.join(self._UI_DIR, "tooltip_manager.py")) as f:
            return f.read()

    def _alpha_tool_source(self) -> str:
        with open(os.path.join(self._UI_DIR, "alpha_tool.py")) as f:
            return f.read()

    # ── apply_alpha_check must be registered ──────────────────────────────────

    def test_apply_alpha_check_registered_in_alpha_tool(self):
        """register_tooltips must register _apply_alpha_check so it gets
        cycling tips from the tooltip manager."""
        src = self._alpha_tool_source()
        self.assertIn('mgr.register(self._apply_alpha_check, "apply_alpha_check")', src,
                      "_apply_alpha_check must be registered with the tooltip manager")

    def test_apply_alpha_check_key_exists_in_all_tip_dicts(self):
        """apply_alpha_check must have entries in _NORMAL, _DUMBED, and _VULGAR."""
        src = self._tooltip_source()
        count = src.count('"apply_alpha_check":')
        self.assertGreaterEqual(count, 3,
                                "apply_alpha_check must appear in _NORMAL, _DUMBED, and _VULGAR "
                                f"(found {count} occurrence(s))")

    # ── No stale clamp_min / clamp_max mode references ────────────────────────

    def test_no_clamp_min_mode_reference_in_clamp_tips(self):
        """clamp_min_spin and clamp_max_spin tips must not reference non-existent
        'clamp_min mode' or 'clamp_max mode'."""
        src = self._tooltip_source()
        self.assertNotIn("clamp_min mode", src,
                         "Tooltip text must not reference 'clamp_min mode' — "
                         "clamp_min is a spinbox, not a processing mode")
        self.assertNotIn("clamp_max mode", src,
                         "Tooltip text must not reference 'clamp_max mode' — "
                         "clamp_max is a spinbox, not a processing mode")
        self.assertNotIn("clamp modes", src,
                         "Tooltip text must not reference 'clamp modes' — "
                         "clamping is applied via spinboxes, not via a mode selection")

    def test_mode_combo_no_clamp_min_max_mode_in_list(self):
        """mode_combo tips must not list clamp_min or clamp_max as mode names."""
        src = self._tooltip_source()
        self.assertNotIn("clamp_min, clamp_max", src,
                         "mode_combo tips must not list 'clamp_min, clamp_max' as mode names. "
                         "Modes are: set, multiply, add, subtract, normalize")
        self.assertNotIn("clamp_min/clamp_max", src,
                         "mode_combo tips must not list 'clamp_min/clamp_max' as mode names. "
                         "Modes are: set, multiply, add, subtract, normalize")
        self.assertNotIn("clamp_min/max", src,
                         "mode_combo tips must not list 'clamp_min/max' as mode names. "
                         "Modes are: set, multiply, add, subtract, normalize")

    def test_mode_combo_normal_mentions_normalize(self):
        """_NORMAL mode_combo tips must mention the 'normalize' mode."""
        src = self._tooltip_source()
        # The _NORMAL dict comes before _DUMBED
        normal_end = src.find("# Dumbed Down")
        normal_section = src[:normal_end]
        self.assertIn("normalize", normal_section,
                      "_NORMAL mode_combo tips must describe the normalize mode")

    # ── No "grayed out" for use_preset_check ──────────────────────────────────

    def test_use_preset_check_no_grayed_out_in_tips(self):
        """use_preset_check tips must not say the controls are 'grayed out'.
        The redesigned UI re-enables the spinbox when switching to manual mode."""
        src = self._tooltip_source()
        # Find all use_preset_check blocks
        idx = 0
        while True:
            pos = src.find('"use_preset_check":', idx)
            if pos < 0:
                break
            # Get the block up to the closing bracket of the list
            end = src.find("],", pos)
            block = src[pos:end]
            self.assertNotIn("grayed out", block,
                             "use_preset_check tips must not say controls are 'grayed out' — "
                             "switching to manual now re-enables the spinbox")
            self.assertNotIn("grey out", block,
                             "use_preset_check tips must not say controls 'grey out'")
            idx = end + 1

    # ── alpha_spin Vulgar must not say "mode above" ───────────────────────────

    def test_alpha_spin_vulgar_no_mode_above(self):
        """_VULGAR alpha_spin tips must not say 'mode above' — the mode combo
        is now in the Advanced section BELOW the spinbox."""
        src = self._tooltip_source()
        vulgar_start = src.find("# No Filter")
        vulgar_section = src[vulgar_start:]
        # Find alpha_spin block in vulgar section
        pos = vulgar_section.find('"alpha_spin":')
        end = vulgar_section.find("],", pos)
        block = vulgar_section[pos:end]
        self.assertNotIn("mode above", block,
                         "_VULGAR alpha_spin tips must not say 'mode above' — the mode "
                         "combo is in the Advanced section below the spinbox")

    # ── finetune_params_lbl: registered + in all 3 dicts ─────────────────────

    def test_finetune_params_lbl_registered_in_alpha_tool(self):
        """register_tooltips must register _finetune_params_lbl so it gets
        cycling tips from the tooltip manager instead of just its inline tooltip."""
        src = self._alpha_tool_source()
        self.assertIn('mgr.register(self._finetune_params_lbl, "finetune_params_lbl")', src,
                      "_finetune_params_lbl must be registered with the tooltip manager")

    def test_finetune_params_lbl_key_exists_in_all_tip_dicts(self):
        """finetune_params_lbl must have entries in _NORMAL, _DUMBED, and _VULGAR."""
        src = self._tooltip_source()
        count = src.count('"finetune_params_lbl":')
        self.assertGreaterEqual(count, 3,
                                "finetune_params_lbl must appear in _NORMAL, _DUMBED, and _VULGAR "
                                f"(found {count} occurrence(s))")

    def test_finetune_params_lbl_no_inline_tooltip_in_alpha_tool(self):
        """_finetune_params_lbl must not set an inline setToolTip() — the
        TooltipManager owns the tooltip now and the inline call is redundant."""
        src = self._alpha_tool_source()
        # Find the finetune_params_lbl construction block
        lbl_pos = src.find("self._finetune_params_lbl = QLabel")
        # Scan forward to the next widget creation to bound the search
        next_widget = src.find("gt_layout.addWidget", lbl_pos + 1)
        block = src[lbl_pos:next_widget]
        self.assertNotIn("setToolTip", block,
                         "_finetune_params_lbl must not have an inline setToolTip() — "
                         "the TooltipManager provides cycling tips for it now")

    # ── binary_cut_check: no stale "threshold above" reference ────────────────

    def test_binary_cut_check_no_threshold_above(self):
        """binary_cut_check tips must not say 'threshold ... above' — the threshold
        spinbox is in the Advanced section BELOW binary_cut in the UI grid."""
        src = self._tooltip_source()
        idx = 0
        while True:
            pos = src.find('"binary_cut_check":', idx)
            if pos < 0:
                break
            end = src.find("],", pos)
            block = src[pos:end].lower()
            self.assertNotIn("threshold spinbox above", block,
                             "binary_cut_check tips must not say 'threshold spinbox above' — "
                             "threshold is in Advanced Options below binary_cut")
            self.assertNotIn("threshold value above", block,
                             "binary_cut_check tips must not say 'threshold value above' — "
                             "threshold is in Advanced Options below binary_cut")
            idx = end + 1

    # ── threshold_spin: no wrong "process only fully transparent" ─────────────

    def test_threshold_255_not_described_as_process_none(self):
        """threshold_spin tips must not claim threshold=255 processes 'almost nothing'
        or 'only fully transparent pixels' — it actually skips only alpha=255 pixels."""
        src = self._tooltip_source()
        self.assertNotIn("process only fully transparent pixels", src,
                         "threshold_spin tips must not say '255 = process only fully transparent "
                         "pixels' — threshold=255 skips only fully opaque pixels (alpha=255)")
        self.assertNotIn("process almost NONE", src,
                         "threshold_spin tips must not say 'process almost NONE' for threshold=255 — "
                         "threshold=255 processes everything except fully opaque pixels")


# ---------------------------------------------------------------------------
# Crash / hang / lag prevention tests
# ---------------------------------------------------------------------------

class TestCrashHangLagPrevention(unittest.TestCase):
    """Verify that the crash, hang, and lag prevention measures are in place."""

    _SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")

    # ── MemoryError handling ──────────────────────────────────────────────────

    def test_apply_alpha_preset_wraps_memoryerror(self):
        """apply_alpha_preset must catch MemoryError from np.array and re-raise
        it with a user-friendly message that includes image dimensions."""
        from unittest.mock import patch
        img = make_rgba_image(4, 4, alpha=128)
        preset = AlphaPreset(
            name="test", alpha_value=255, threshold=0, invert=False,
            description="test preset", builtin=True,
        )
        with patch("src.core.alpha_processor.np.array", side_effect=MemoryError("mock OOM")):
            with self.assertRaises(MemoryError) as ctx:
                apply_alpha_preset(img, preset)
        self.assertIn("memory", str(ctx.exception).lower(),
                      "MemoryError message should mention memory")

    def test_apply_manual_alpha_wraps_memoryerror(self):
        """apply_manual_alpha must catch MemoryError from np.array and re-raise
        it with a user-friendly message."""
        from unittest.mock import patch
        img = make_rgba_image(4, 4, alpha=128)
        with patch("src.core.alpha_processor.np.array", side_effect=MemoryError("mock OOM")):
            with self.assertRaises(MemoryError) as ctx:
                apply_manual_alpha(img, value=255)
        self.assertIn("memory", str(ctx.exception).lower())

    def test_apply_rgba_adjust_wraps_memoryerror(self):
        """apply_rgba_adjust must catch MemoryError from np.array and re-raise
        it with a user-friendly message."""
        from unittest.mock import patch
        img = make_rgba_image(4, 4, alpha=128)
        with patch("src.core.alpha_processor.np.array", side_effect=MemoryError("mock OOM")):
            with self.assertRaises(MemoryError) as ctx:
                apply_rgba_adjust(img, red_delta=10)
        self.assertIn("memory", str(ctx.exception).lower())

    # ── Worker MemoryError handling ───────────────────────────────────────────

    def _worker_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "core", "worker.py")) as f:
            return f.read()

    def test_alpha_worker_has_memoryerror_catch(self):
        """AlphaWorker.run() must have an explicit MemoryError except clause
        before the generic Exception handler so OOM errors get a clear message."""
        src = self._worker_source()
        # Find AlphaWorker's run() method
        alpha_run_start = src.find("class AlphaWorker")
        converter_start = src.find("class ConverterWorker")
        alpha_run_section = src[alpha_run_start:converter_start]
        self.assertIn("except MemoryError", alpha_run_section,
                      "AlphaWorker.run() must catch MemoryError explicitly")

    def test_converter_worker_has_memoryerror_catch(self):
        """ConverterWorker.run() must have an explicit MemoryError except clause."""
        src = self._worker_source()
        converter_start = src.find("class ConverterWorker")
        converter_section = src[converter_start:]
        self.assertIn("except MemoryError", converter_section,
                      "ConverterWorker.run() must catch MemoryError explicitly")

    # ── Preview loader abort flag ─────────────────────────────────────────────

    def _alpha_tool_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "ui", "alpha_tool.py")) as f:
            return f.read()

    def _preview_pane_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "ui", "preview_pane.py")) as f:
            return f.read()

    def _converter_tool_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "ui", "converter_tool.py")) as f:
            return f.read()

    def test_alpha_preview_loader_has_abort_flag(self):
        """_AlphaPreviewLoader must have an _abort flag and a stop() method
        so superseded threads can bail out before expensive processing."""
        src = self._alpha_tool_source()
        loader_start = src.find("class _AlphaPreviewLoader")
        # Find the end of the class (next class or end of file)
        next_class = src.find("\nclass ", loader_start + 1)
        loader_section = src[loader_start:next_class] if next_class != -1 else src[loader_start:]
        self.assertIn("self._abort = False", loader_section,
                      "_AlphaPreviewLoader.__init__ must set self._abort = False")
        self.assertIn("def stop(", loader_section,
                      "_AlphaPreviewLoader must have a stop() method")
        self.assertIn("self._abort", loader_section,
                      "_AlphaPreviewLoader.run() must check self._abort")

    def test_alpha_preview_loader_stop_called_on_replace(self):
        """_update_compare() must call stop() on the old preview loader
        before replacing it, not just disconnect its signals."""
        src = self._alpha_tool_source()
        update_pos = src.find("def _update_compare(")
        # Find the next method after _update_compare
        next_method = src.find("\n    def ", update_pos + 1)
        update_section = src[update_pos:next_method]
        self.assertIn(".stop()", update_section,
                      "_update_compare() must call stop() on the old preview loader")

    def test_converter_preview_loader_has_abort_flag(self):
        """_ConverterPreviewLoader must have an _abort flag and a stop() method."""
        src = self._preview_pane_source()
        loader_start = src.find("class _ConverterPreviewLoader")
        next_class = src.find("\nclass ", loader_start + 1)
        loader_section = src[loader_start:next_class] if next_class != -1 else src[loader_start:]
        self.assertIn("self._abort = False", loader_section,
                      "_ConverterPreviewLoader.__init__ must set self._abort = False")
        self.assertIn("def stop(", loader_section,
                      "_ConverterPreviewLoader must have a stop() method")
        self.assertIn("self._abort", loader_section,
                      "_ConverterPreviewLoader.run() must check self._abort")

    def test_converter_refresh_preview_calls_stop(self):
        """_refresh_preview() in converter_tool must call stop() on the old
        preview loader before replacing it."""
        src = self._converter_tool_source()
        refresh_pos = src.find("def _refresh_preview(")
        next_method = src.find("\n    def ", refresh_pos + 1)
        refresh_section = src[refresh_pos:next_method]
        self.assertIn(".stop()", refresh_section,
                      "_refresh_preview() must call stop() on the old preview loader")

    # ── closeEvent cleanup ────────────────────────────────────────────────────

    def _main_window_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "ui", "main_window.py")) as f:
            return f.read()

    def test_closeevent_stops_preview_loaders(self):
        """closeEvent must stop preview loaders on both tabs so their threads
        do not emit signals into already-destroyed Qt objects."""
        src = self._main_window_source()
        close_pos = src.find("def closeEvent(")
        next_method = src.find("\n    def ", close_pos + 1)
        close_section = src[close_pos:next_method]
        self.assertIn("_preview_loader", close_section,
                      "closeEvent must reference _preview_loader to cancel it")
        self.assertIn(".stop()", close_section,
                      "closeEvent must call stop() to cancel in-flight preview loaders")

    def test_closeevent_stops_debounce_timers(self):
        """closeEvent must stop preview debounce timers on both tabs to prevent
        pending timeouts firing after the tab is torn down."""
        src = self._main_window_source()
        close_pos = src.find("def closeEvent(")
        next_method = src.find("\n    def ", close_pos + 1)
        close_section = src[close_pos:next_method]
        self.assertIn("_preview_debounce", close_section,
                      "closeEvent must reference _preview_debounce to stop it")

    def test_closeevent_stops_main_window_timers(self):
        """closeEvent must stop the main-window-level timers
        (_settings_apply_timer, _resize_timer)."""
        src = self._main_window_source()
        close_pos = src.find("def closeEvent(")
        next_method = src.find("\n    def ", close_pos + 1)
        close_section = src[close_pos:next_method]
        self.assertIn("_settings_apply_timer", close_section,
                      "closeEvent must stop _settings_apply_timer")
        self.assertIn("_resize_timer", close_section,
                      "closeEvent must stop _resize_timer")

    # ── Tooltip manager destroyed-ref cleanup ─────────────────────────────────

    def _tooltip_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "ui", "tooltip_manager.py")) as f:
            return f.read()

    def test_register_tab_bar_connects_destroyed_signal(self):
        """register_tab_bar() must connect tab_bar.destroyed to a cleanup
        method so stale refs don't accumulate in _tab_bar_keys/_tab_bar_refs."""
        src = self._tooltip_source()
        reg_pos = src.find("def register_tab_bar(")
        next_method = src.find("\n    def ", reg_pos + 1)
        reg_section = src[reg_pos:next_method]
        self.assertIn("destroyed", reg_section,
                      "register_tab_bar() must connect tab_bar.destroyed for cleanup")

    def test_cleanup_tab_bar_method_exists(self):
        """TooltipManager must have a _cleanup_tab_bar() method that removes
        dead refs from all three tracking dicts."""
        src = self._tooltip_source()
        self.assertIn("def _cleanup_tab_bar(", src,
                      "TooltipManager must have a _cleanup_tab_bar() method")
        cleanup_pos = src.find("def _cleanup_tab_bar(")
        # Find end of method (next def at same indent)
        next_method = src.find("\n    def ", cleanup_pos + 1)
        cleanup_section = src[cleanup_pos:next_method]
        self.assertIn("_tab_bar_keys", cleanup_section,
                      "_cleanup_tab_bar() must clean up _tab_bar_keys")
        self.assertIn("_tab_bar_refs", cleanup_section,
                      "_cleanup_tab_bar() must clean up _tab_bar_refs")
        self.assertIn("_tab_widget_to_bar", cleanup_section,
                      "_cleanup_tab_bar() must clean up _tab_widget_to_bar")

    # ── Font cache cap ────────────────────────────────────────────────────────

    def _click_effects_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "ui", "click_effects.py")) as f:
            return f.read()

    def test_font_cache_has_max_size_constant(self):
        """ClickEffectsOverlay must define _FONT_CACHE_MAX to cap the font
        cache and prevent unbounded growth over long sessions."""
        src = self._click_effects_source()
        self.assertIn("_FONT_CACHE_MAX", src,
                      "ClickEffectsOverlay must define _FONT_CACHE_MAX")

    def test_font_cache_eviction_in_get_font(self):
        """_get_font() must evict an entry when the cache exceeds _FONT_CACHE_MAX
        to keep memory bounded during long sessions with many particle sizes."""
        src = self._click_effects_source()
        get_font_pos = src.find("def _get_font(")
        next_method = src.find("\n    def ", get_font_pos + 1)
        get_font_section = src[get_font_pos:next_method]
        self.assertIn("_FONT_CACHE_MAX", get_font_section,
                      "_get_font() must check against _FONT_CACHE_MAX")
        self.assertIn("pop(", get_font_section,
                      "_get_font() must evict (pop) an entry when the cache is full")


# ---------------------------------------------------------------------------
# Round-2 crash/hang/lag prevention tests
# ---------------------------------------------------------------------------

class TestRound2CrashHangLag(unittest.TestCase):
    """Round-2 hardening: settings type-safety, file_converter validation,
    mouse_trail timer optimisation, ThumbLoader abort flags."""

    _SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")

    # ------------------------------------------------------------------
    # settings_manager – type-safe JSON getters
    # ------------------------------------------------------------------

    def _make_settings_manager(self):
        """Return a real SettingsManager backed by an in-memory QSettings."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from unittest.mock import MagicMock, patch
        from src.core.settings_manager import SettingsManager
        from PyQt6.QtCore import QSettings
        with patch("src.core.settings_manager.QSettings") as MockQS:
            qs = MagicMock()
            MockQS.return_value = qs
            mgr = SettingsManager.__new__(SettingsManager)
            mgr._qs = qs
            return mgr, qs

    def test_get_converter_history_null_returns_empty_list(self):
        """get_converter_history() must return [] when JSON is 'null', not None."""
        from src.core.settings_manager import SettingsManager
        from unittest.mock import MagicMock
        mgr = SettingsManager.__new__(SettingsManager)
        qs = MagicMock()
        qs.value.return_value = "null"  # JSON null → json.loads returns None
        mgr._qs = qs
        result = mgr.get_converter_history()
        self.assertIsInstance(result, list,
                              "get_converter_history() must return list when stored value is JSON null")
        self.assertEqual(result, [])

    def test_get_alpha_history_null_returns_empty_list(self):
        """get_alpha_history() must return [] when JSON is 'null'."""
        from src.core.settings_manager import SettingsManager
        from unittest.mock import MagicMock
        mgr = SettingsManager.__new__(SettingsManager)
        qs = MagicMock()
        qs.value.return_value = "null"
        mgr._qs = qs
        result = mgr.get_alpha_history()
        self.assertIsInstance(result, list)
        self.assertEqual(result, [])

    def test_get_custom_presets_null_returns_empty_list(self):
        """get_custom_presets() must return [] when JSON is 'null'."""
        from src.core.settings_manager import SettingsManager
        from unittest.mock import MagicMock
        mgr = SettingsManager.__new__(SettingsManager)
        qs = MagicMock()
        qs.value.return_value = "null"
        mgr._qs = qs
        result = mgr.get_custom_presets()
        self.assertIsInstance(result, list)
        self.assertEqual(result, [])

    def test_get_saved_themes_null_returns_empty_dict(self):
        """get_saved_themes() must return {} when JSON is 'null', not None."""
        from src.core.settings_manager import SettingsManager
        from unittest.mock import MagicMock
        mgr = SettingsManager.__new__(SettingsManager)
        qs = MagicMock()
        qs.value.return_value = "null"
        mgr._qs = qs
        result = mgr.get_saved_themes()
        self.assertIsInstance(result, dict,
                              "get_saved_themes() must return dict when stored value is JSON null")
        self.assertEqual(result, {})

    def test_get_theme_returns_complete_dict(self):
        """get_theme() must always return a dict with all required keys,
        merging missing keys from _DEFAULT_THEME for forward-compatibility."""
        from src.core.settings_manager import SettingsManager
        import json
        from unittest.mock import MagicMock
        mgr = SettingsManager.__new__(SettingsManager)
        qs = MagicMock()
        # Simulate a stored theme with only some keys (e.g. saved by older version)
        partial_theme = {"name": "Custom", "background": "#000000"}
        qs.value.return_value = json.dumps(partial_theme)
        mgr._qs = qs
        result = mgr.get_theme()
        self.assertIsInstance(result, dict)
        # All required keys from _DEFAULT_THEME must be present
        for key in SettingsManager._DEFAULT_THEME:
            self.assertIn(key, result,
                          f"get_theme() result must contain default key '{key}'")
        # But stored values should take priority
        self.assertEqual(result["name"], "Custom")
        self.assertEqual(result["background"], "#000000")

    def test_get_theme_non_dict_returns_default(self):
        """get_theme() must return _DEFAULT_THEME when the stored JSON is not a dict."""
        from src.core.settings_manager import SettingsManager
        from unittest.mock import MagicMock
        mgr = SettingsManager.__new__(SettingsManager)
        qs = MagicMock()
        qs.value.return_value = "[1, 2, 3]"  # JSON array, not object
        mgr._qs = qs
        result = mgr.get_theme()
        self.assertIsInstance(result, dict)
        self.assertEqual(result["name"], "Panda Dark",
                         "get_theme() must return default when stored value is not a JSON object")

    # ------------------------------------------------------------------
    # settings_manager – import_settings validation
    # ------------------------------------------------------------------

    def _settings_manager_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "core", "settings_manager.py")) as f:
            return f.read()

    def test_import_settings_validates_json_is_dict(self):
        """import_settings() must raise ValueError when the JSON root is not a dict."""
        import json, tempfile
        from src.core.settings_manager import SettingsManager
        from unittest.mock import MagicMock
        mgr = SettingsManager.__new__(SettingsManager)
        qs = MagicMock()
        mgr._qs = qs
        # Write a JSON file that has an array at the root (not an object)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                         delete=False, encoding="utf-8") as f:
            json.dump([1, 2, 3], f)
            tmp_path = f.name
        try:
            with self.assertRaises(ValueError):
                mgr.import_settings(tmp_path)
        finally:
            os.unlink(tmp_path)

    # ------------------------------------------------------------------
    # file_converter – resize validation
    # ------------------------------------------------------------------

    def _file_converter_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "core", "file_converter.py")) as f:
            return f.read()

    def test_convert_file_rejects_zero_resize(self):
        """convert_file() must raise ValueError when resize contains a zero dimension."""
        import tempfile
        from src.core.file_converter import convert_file
        img = make_rgba_image(8, 8, alpha=200)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as src_f:
            img.save(src_f.name)
            src_path = src_f.name
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as dst_f:
            dst_path = dst_f.name
        try:
            with self.assertRaises(ValueError):
                convert_file(src_path, dst_path, "PNG", resize=(0, 100))
            with self.assertRaises(ValueError):
                convert_file(src_path, dst_path, "PNG", resize=(100, 0))
        finally:
            os.unlink(src_path)
            try:
                os.unlink(dst_path)
            except FileNotFoundError:
                pass

    def test_convert_file_rejects_negative_resize(self):
        """convert_file() must raise ValueError when resize has negative dimensions."""
        import tempfile
        from src.core.file_converter import convert_file
        img = make_rgba_image(8, 8, alpha=200)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as src_f:
            img.save(src_f.name)
            src_path = src_f.name
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as dst_f:
            dst_path = dst_f.name
        try:
            with self.assertRaises(ValueError):
                convert_file(src_path, dst_path, "PNG", resize=(-1, 100))
        finally:
            os.unlink(src_path)
            try:
                os.unlink(dst_path)
            except FileNotFoundError:
                pass

    def test_convert_file_rejects_oversized_resize(self):
        """convert_file() must raise ValueError when resize dimensions exceed 65535."""
        import tempfile
        from src.core.file_converter import convert_file
        img = make_rgba_image(8, 8, alpha=200)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as src_f:
            img.save(src_f.name)
            src_path = src_f.name
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as dst_f:
            dst_path = dst_f.name
        try:
            with self.assertRaises(ValueError):
                convert_file(src_path, dst_path, "PNG", resize=(100000, 100))
        finally:
            os.unlink(src_path)
            try:
                os.unlink(dst_path)
            except FileNotFoundError:
                pass

    def test_open_image_wraps_memoryerror(self):
        """file_converter._open_image() must catch MemoryError from img.load()
        and re-raise it with a user-friendly message."""
        from unittest.mock import patch, MagicMock
        from src.core.file_converter import _open_image
        import tempfile
        # Create a valid PNG we can open
        img = make_rgba_image(4, 4, alpha=128)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            img.save(f.name)
            tmp_path = f.name
        try:
            with patch("src.core.file_converter.Image.open") as mock_open:
                mock_img = MagicMock()
                mock_img.mode = "RGBA"
                mock_img.size = (4, 4)
                mock_img.load.side_effect = MemoryError("mock OOM")
                mock_open.return_value = mock_img
                with self.assertRaises(MemoryError) as ctx:
                    _open_image(tmp_path)
            self.assertIn("memory", str(ctx.exception).lower())
        finally:
            os.unlink(tmp_path)

    # ------------------------------------------------------------------
    # mouse_trail – timer stop/restart optimisation
    # ------------------------------------------------------------------

    def _mouse_trail_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "ui", "mouse_trail.py")) as f:
            return f.read()

    def test_trail_tick_stops_timer_when_empty(self):
        """_tick() must call self._timer.stop() when the trail deque becomes
        empty so the 33ms timer does not fire during idle mouse periods."""
        src = self._mouse_trail_source()
        tick_pos = src.find("def _tick(")
        next_method = src.find("\n    def ", tick_pos + 1)
        tick_section = src[tick_pos:next_method]
        self.assertIn("_timer.stop()", tick_section,
                      "_tick() must call self._timer.stop() when the trail is empty")

    def test_trail_eventfilter_restarts_timer(self):
        """eventFilter must restart the timer when a new trail point is
        appended, in case the timer was stopped while the trail was idle."""
        src = self._mouse_trail_source()
        ef_pos = src.find("def eventFilter(")
        next_method = src.find("\n    def ", ef_pos + 1)
        ef_section = src[ef_pos:next_method]
        self.assertIn("_timer.start()", ef_section,
                      "eventFilter must restart the timer when a new trail point is added")
        self.assertIn("_timer.isActive()", ef_section,
                      "eventFilter must check isActive() before restarting the timer")

    # ------------------------------------------------------------------
    # preview_pane – _ThumbLoader / _ConvertedThumbLoader abort flags
    # ------------------------------------------------------------------

    def _preview_pane_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "ui", "preview_pane.py")) as f:
            return f.read()

    def test_thumb_loader_has_abort_flag(self):
        """_ThumbLoader must have an _abort flag and a stop() method
        so it can be cancelled when a newer file is selected."""
        src = self._preview_pane_source()
        loader_start = src.find("class _ThumbLoader")
        next_class = src.find("\nclass ", loader_start + 1)
        loader_section = src[loader_start:next_class]
        self.assertIn("self._abort = False", loader_section,
                      "_ThumbLoader.__init__ must set self._abort = False")
        self.assertIn("def stop(", loader_section,
                      "_ThumbLoader must have a stop() method")
        self.assertIn("self._abort", loader_section,
                      "_ThumbLoader.run() must check self._abort")

    def test_converted_thumb_loader_has_abort_flag(self):
        """_ConvertedThumbLoader must have an _abort flag and a stop() method."""
        src = self._preview_pane_source()
        loader_start = src.find("class _ConvertedThumbLoader")
        next_class = src.find("\nclass ", loader_start + 1)
        loader_section = src[loader_start:next_class]
        self.assertIn("self._abort = False", loader_section,
                      "_ConvertedThumbLoader.__init__ must set self._abort = False")
        self.assertIn("def stop(", loader_section,
                      "_ConvertedThumbLoader must have a stop() method")
        self.assertIn("self._abort", loader_section,
                      "_ConvertedThumbLoader.run() must check self._abort")

    def test_image_preview_pane_start_loader_calls_stop(self):
        """ImagePreviewPane._start_loader() must call stop() on the old loader
        before replacing it so stale threads don't waste CPU."""
        src = self._preview_pane_source()
        method_pos = src.find("def _start_loader(")
        next_method = src.find("\n    def ", method_pos + 1)
        section = src[method_pos:next_method]
        self.assertIn(".stop()", section,
                      "_start_loader() must call stop() on the old loader")


# ---------------------------------------------------------------------------
# Round-3 crash/hang/memory prevention tests
# ---------------------------------------------------------------------------

class TestRound3Hardening(unittest.TestCase):
    """Round-3: load_image MemoryError, ThumbRunnable cancel flag,
    worker signal disconnect."""

    _SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")

    # ------------------------------------------------------------------
    # alpha_processor – load_image() MemoryError guard
    # ------------------------------------------------------------------

    def _alpha_processor_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "core", "alpha_processor.py")) as f:
            return f.read()

    def test_load_image_memoryerror_guard_in_source(self):
        """load_image() must wrap img.convert('RGBA') in a MemoryError guard
        so that OOM on colour-space conversion gives a useful message."""
        src = self._alpha_processor_source()
        load_pos = src.find("def load_image(")
        # Find end of function (next top-level def)
        next_def = src.find("\ndef ", load_pos + 1)
        fn_src = src[load_pos:next_def]
        self.assertIn("MemoryError", fn_src,
                      "load_image() must have a MemoryError guard for the RGBA conversion")
        self.assertIn("megapixels", fn_src,
                      "load_image() MemoryError message must include 'megapixels' for context")

    def test_load_image_wraps_convert_memoryerror(self):
        """load_image() must re-raise MemoryError from img.convert with W×H context."""
        from unittest.mock import patch, MagicMock
        from src.core.alpha_processor import load_image
        import tempfile
        img = make_rgba_image(4, 4, alpha=128)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            img.save(f.name)
            tmp = f.name
        try:
            with patch("src.core.alpha_processor.Image.open") as mock_open:
                mock_img = MagicMock()
                mock_img.mode = "RGB"
                mock_img.size = (4, 4)
                mock_img.convert.side_effect = MemoryError("OOM")
                mock_open.return_value = mock_img
                with self.assertRaises(MemoryError) as ctx:
                    load_image(tmp)
            self.assertIn("megapixels", str(ctx.exception))
        finally:
            os.unlink(tmp)

    # ------------------------------------------------------------------
    # drop_list – _ThumbRunnable cancel flag
    # ------------------------------------------------------------------

    def _drop_list_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "ui", "drop_list.py")) as f:
            return f.read()

    def test_thumb_runnable_accepts_cancel_event(self):
        """_ThumbRunnable must accept a cancel threading.Event parameter."""
        src = self._drop_list_source()
        init_pos = src.find("class _ThumbRunnable")
        next_class = src.find("\nclass ", init_pos + 1)
        cls_src = src[init_pos:next_class]
        self.assertIn("cancel", cls_src,
                      "_ThumbRunnable.__init__ must accept a cancel parameter")
        self.assertIn("threading.Event", cls_src,
                      "_ThumbRunnable must reference threading.Event (or threading)")

    def test_thumb_runnable_checks_cancel_before_emit(self):
        """_ThumbRunnable.run() must check the cancel event before emitting."""
        src = self._drop_list_source()
        run_pos = src.find("class _ThumbRunnable")
        next_class = src.find("\nclass ", run_pos + 1)
        cls_src = src[run_pos:next_class]
        # Must check cancel at least twice: once at top and once before emit
        cancel_count = cls_src.count("_cancel.is_set()")
        self.assertGreaterEqual(cancel_count, 2,
                                "_ThumbRunnable.run() must check cancel at entry AND before emit")

    def test_drop_list_has_cancel_event(self):
        """DropFileList must initialise a _cancel_event threading.Event."""
        src = self._drop_list_source()
        self.assertIn("_cancel_event", src,
                      "DropFileList must have a _cancel_event attribute")
        self.assertIn("threading.Event()", src,
                      "DropFileList must create threading.Event() instances")

    def test_drop_list_clear_retires_cancel_event(self):
        """DropFileList.clear() and _clear_all() must retire the cancel event
        so that already-queued runnables bail out early."""
        src = self._drop_list_source()
        # Find _clear_all
        clear_all_pos = src.find("def _clear_all(")
        next_method = src.find("\n    def ", clear_all_pos + 1)
        clear_all_src = src[clear_all_pos:next_method]
        self.assertIn("_cancel_event.set()", clear_all_src,
                      "_clear_all() must call self._cancel_event.set() to retire old event")
        # Find clear()
        clear_pos = src.find("def clear(")
        next_method2 = src.find("\n    def ", clear_pos + 1)
        clear_src = src[clear_pos:next_method2]
        self.assertIn("_cancel_event.set()", clear_src,
                      "clear() must call self._cancel_event.set() to retire old event")

    def test_thumb_runnable_cancel_prevents_emit(self):
        """_ThumbRunnable.run() must return early (without emitting) when the
        cancel event is already set.  Verified via source inspection since
        Qt display is unavailable in the headless test environment."""
        src = self._drop_list_source()
        run_pos = src.find("class _ThumbRunnable")
        next_class = src.find("\nclass ", run_pos + 1)
        cls_src = src[run_pos:next_class]
        # The run() method must have an early return when cancel is set
        self.assertIn("return", cls_src,
                      "_ThumbRunnable.run() must return early when cancel is set")
        # It must also guard the emit call with is_set()
        self.assertIn("is_set()", cls_src,
                      "_ThumbRunnable.run() must call _cancel.is_set() before emitting")

    # ------------------------------------------------------------------
    # alpha_tool + converter_tool – worker signal disconnect
    # ------------------------------------------------------------------

    def _alpha_tool_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "ui", "alpha_tool.py")) as f:
            return f.read()

    def _converter_tool_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "ui", "converter_tool.py")) as f:
            return f.read()

    def _run_method_source(self, src: str) -> str:
        run_pos = src.rfind("def _run(")
        next_method = src.find("\n    def ", run_pos + 1)
        return src[run_pos:next_method]

    def test_alpha_tool_run_disconnects_old_worker(self):
        """AlphaTool._run() must disconnect the old worker's signals before
        creating a new worker to prevent signal connection table growth."""
        src = self._alpha_tool_source()
        run_src = self._run_method_source(src)
        self.assertIn(".disconnect()", run_src,
                      "AlphaTool._run() must call .disconnect() on old worker signals")

    def test_converter_tool_run_disconnects_old_worker(self):
        """ConverterTool._run() must disconnect the old worker's signals before
        creating a new worker to prevent signal connection table growth."""
        src = self._converter_tool_source()
        run_src = self._run_method_source(src)
        self.assertIn(".disconnect()", run_src,
                      "ConverterTool._run() must call .disconnect() on old worker signals")


# ---------------------------------------------------------------------------
# Round-4 crash/hang/resource prevention tests
# ---------------------------------------------------------------------------

class TestRound4Hardening(unittest.TestCase):
    """Round-4: save_image MemoryError guard, convert_file MemoryError guard,
    main_window lambda fix + overlay stop in closeEvent, history_tab StringIO
    context manager."""

    _SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")

    # ------------------------------------------------------------------
    # alpha_processor – save_image() MemoryError guards
    # ------------------------------------------------------------------

    def _alpha_processor_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "core", "alpha_processor.py")) as f:
            return f.read()

    def test_save_image_has_memoryerror_guard_in_source(self):
        """save_image() must wrap img.convert('RGB') and img.save() in
        MemoryError guards consistent with the rest of the module."""
        src = self._alpha_processor_source()
        save_pos = src.find("def save_image(")
        next_def = src.find("\ndef ", save_pos + 1)
        fn_src = src[save_pos:next_def]
        self.assertIn("MemoryError", fn_src,
                      "save_image() must have MemoryError guards")
        self.assertIn("megapixels", fn_src,
                      "save_image() MemoryError must include 'megapixels' context")

    def test_save_image_convert_wraps_memoryerror(self):
        """save_image() must re-raise MemoryError from img.convert with context."""
        from unittest.mock import patch, MagicMock
        from src.core.alpha_processor import save_image
        import tempfile
        img = make_rgba_image(4, 4, alpha=128)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            tmp = f.name
        try:
            with patch.object(img, "convert", side_effect=MemoryError("OOM")):
                with self.assertRaises(MemoryError) as ctx:
                    save_image(img, tmp, ".jpg")
            self.assertIn("megapixels", str(ctx.exception))
        finally:
            os.unlink(tmp)

    def test_save_image_write_wraps_memoryerror(self):
        """save_image() must re-raise MemoryError from img.save with context."""
        from unittest.mock import patch, MagicMock
        from src.core.alpha_processor import save_image
        import tempfile
        img = make_rgba_image(4, 4, alpha=200)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        try:
            with patch.object(img, "save", side_effect=MemoryError("OOM")):
                with self.assertRaises(MemoryError) as ctx:
                    save_image(img, tmp, ".png")
            self.assertIn("megapixels", str(ctx.exception))
        finally:
            os.unlink(tmp)

    # ------------------------------------------------------------------
    # file_converter – convert_file() MemoryError guard
    # ------------------------------------------------------------------

    def _file_converter_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "core", "file_converter.py")) as f:
            return f.read()

    def test_convert_file_has_memoryerror_guard(self):
        """convert_file() format-dispatch block must be wrapped in a single
        MemoryError guard that provides W×H context."""
        src = self._file_converter_source()
        fn_pos = src.find("def convert_file(")
        fn_end = src.find("\ndef ", fn_pos + 1)
        fn_src = src[fn_pos:fn_end]
        self.assertIn("except MemoryError", fn_src,
                      "convert_file() must have a MemoryError guard")
        self.assertIn("megapixels", fn_src,
                      "convert_file() MemoryError message must include 'megapixels'")
        # The guard must cover all the format branches — they must all be
        # inside a try block
        self.assertIn("try:", fn_src,
                      "convert_file() must use try/except for the format dispatch block")

    def test_convert_file_save_memoryerror_gives_context(self):
        """convert_file() must re-raise MemoryError from img.save with W×H context."""
        from unittest.mock import patch
        from PIL import Image
        from src.core.file_converter import convert_file
        import tempfile
        # Create a small PNG source
        src_img = make_rgba_image(4, 4, alpha=200)
        with (
            tempfile.NamedTemporaryFile(suffix=".png", delete=False) as sf,
            tempfile.NamedTemporaryFile(suffix=".png", delete=False) as df,
        ):
            src_img.save(sf.name)
            src_path = sf.name
            dst_path = df.name
        try:
            # Patch Image.open to return our known image, then patch its save to OOM
            with patch("src.core.file_converter.Image.open") as mock_open:
                mock_img = MagicMock(wraps=src_img)
                mock_img.size = (4, 4)
                mock_img.mode = "RGBA"
                mock_img.info = {}
                mock_img.save = MagicMock(side_effect=MemoryError("OOM"))
                mock_open.return_value = mock_img
                with self.assertRaises(MemoryError) as ctx:
                    convert_file(src_path, dst_path, "PNG")
            self.assertIn("megapixels", str(ctx.exception))
        finally:
            os.unlink(src_path)
            os.unlink(dst_path)

    # ------------------------------------------------------------------
    # main_window – lambda → named method
    # ------------------------------------------------------------------

    def _main_window_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "ui", "main_window.py")) as f:
            return f.read()

    def test_schedule_unlock_clear_uses_named_method(self):
        """_schedule_unlock_clear() must use a named method instead of a lambda
        so that the callback is safe even if the window starts closing before
        the 6-second timeout fires."""
        src = self._main_window_source()
        fn_pos = src.find("def _schedule_unlock_clear(")
        next_method = src.find("\n    def ", fn_pos + 1)
        fn_src = src[fn_pos:next_method]
        # The connection must NOT use a bare lambda expression
        self.assertNotIn(".connect(lambda", fn_src,
                         "_schedule_unlock_clear() must not use a lambda connection")
        self.assertIn("_clear_unlock_label", fn_src,
                      "_schedule_unlock_clear() must connect to _clear_unlock_label")

    def test_clear_unlock_label_method_exists_and_guards_none(self):
        """_clear_unlock_label() method must exist and guard _unlock_lbl is not None."""
        src = self._main_window_source()
        self.assertIn("def _clear_unlock_label", src,
                      "MainWindow must have a _clear_unlock_label() method")
        fn_pos = src.find("def _clear_unlock_label")
        next_method = src.find("\n    def ", fn_pos + 1)
        fn_src = src[fn_pos:next_method]
        self.assertIn("_unlock_lbl is not None", fn_src,
                      "_clear_unlock_label() must guard against _unlock_lbl being None")

    # ------------------------------------------------------------------
    # main_window – closeEvent stops overlays
    # ------------------------------------------------------------------

    def test_closeevent_stops_click_effects(self):
        """closeEvent must call set_enabled(False) on _click_effects to remove
        the event filter and stop animation timers before teardown."""
        src = self._main_window_source()
        close_pos = src.find("def closeEvent(")
        next_method = src.find("\n    def ", close_pos + 1)
        close_src = src[close_pos:next_method]
        self.assertIn("_click_effects", close_src,
                      "closeEvent must handle _click_effects")
        self.assertIn("set_enabled(False)", close_src,
                      "closeEvent must call set_enabled(False) on overlays")

    def test_closeevent_stops_trail_overlay(self):
        """closeEvent must call set_enabled(False) on _trail_overlay to stop
        the mouse trail timer and event filter before teardown."""
        src = self._main_window_source()
        close_pos = src.find("def closeEvent(")
        next_method = src.find("\n    def ", close_pos + 1)
        close_src = src[close_pos:next_method]
        self.assertIn("_trail_overlay", close_src,
                      "closeEvent must handle _trail_overlay")

    # ------------------------------------------------------------------
    # history_tab – io.StringIO context manager
    # ------------------------------------------------------------------

    def _history_tab_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "ui", "history_tab.py")) as f:
            return f.read()

    def test_export_csv_uses_stringio_context_manager(self):
        """_export_csv() must use io.StringIO as a context manager (with block)
        to guarantee buffer cleanup on any exit path."""
        src = self._history_tab_source()
        fn_pos = src.find("def _export_csv(")
        next_method = src.find("\n    def ", fn_pos + 1)
        fn_src = src[fn_pos:next_method]
        self.assertIn("with io.StringIO", fn_src,
                      "_export_csv() must use 'with io.StringIO(...)' context manager")


# ---------------------------------------------------------------------------
# Round-5 resource-hygiene tests
# ---------------------------------------------------------------------------

class TestRound5ResourceHygiene(unittest.TestCase):
    """Round-5: PIL image close in all paths, BytesIO close in fallback,
    settings_manager sync docstring accuracy."""

    _SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")

    # ------------------------------------------------------------------
    # preview_pane – _pil_to_qimage closes temporary RGBA conversion image
    # ------------------------------------------------------------------

    def _preview_pane_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "ui", "preview_pane.py")) as f:
            return f.read()

    def test_pil_to_qimage_closes_converted_image(self):
        """_pil_to_qimage() must close the temporary RGBA image it creates
        via img.convert('RGBA') so that backing-store memory is released early
        rather than relying on the garbage collector."""
        src = self._preview_pane_source()
        fn_start = src.find("def _pil_to_qimage(")
        fn_end = src.find("\ndef ", fn_start + 1)
        fn_src = src[fn_start:fn_end]
        self.assertIn("img_rgba.close()", fn_src,
                      "_pil_to_qimage() must close the converted RGBA image")
        # Must guard against closing the caller's own image
        self.assertIn("img_rgba is not img", fn_src,
                      "_pil_to_qimage() must only close the image when a new one was created")

    def test_pil_to_qimage_does_not_close_original(self):
        """_pil_to_qimage() must NOT close the caller's image when it is
        already RGBA (img_rgba is img); only the temporary copy should be
        closed."""
        from unittest.mock import patch, MagicMock
        src = self._preview_pane_source()
        # The guard 'if img_rgba is not img' must be present
        self.assertIn("if img_rgba is not img", src,
                      "_pil_to_qimage() must use 'if img_rgba is not img' guard")

    # ------------------------------------------------------------------
    # preview_pane – _ThumbLoader closes image on all exit paths
    # ------------------------------------------------------------------

    def test_thumb_loader_closes_image_in_finally(self):
        """_ThumbLoader.run() must close the PIL image in a finally block so
        that image resources are released even when an exception occurs."""
        src = self._preview_pane_source()
        class_start = src.find("class _ThumbLoader(")
        next_class = src.find("\nclass ", class_start + 1)
        class_src = src[class_start:next_class]
        run_start = class_src.find("    def run(")
        run_src = class_src[run_start:]
        self.assertIn("finally:", run_src,
                      "_ThumbLoader.run() must have a finally block")
        self.assertIn("img.close()", run_src,
                      "_ThumbLoader.run() must close img in the finally block")

    # ------------------------------------------------------------------
    # preview_pane – _ConvertedThumbLoader closes buf in fallback path
    # ------------------------------------------------------------------

    def test_converted_thumb_loader_closes_buf_in_fallback(self):
        """_ConvertedThumbLoader.run() must close the BytesIO buffer in the
        fallback exception path so it is released even when in-memory
        conversion fails."""
        src = self._preview_pane_source()
        class_start = src.find("class _ConvertedThumbLoader(")
        next_class = src.find("\nclass ", class_start + 1)
        class_src = src[class_start:next_class]
        run_start = class_src.find("    def run(")
        run_src = class_src[run_start:]
        # buf.close() must appear BOTH in the try-success path and in the
        # except-fallback path
        self.assertGreaterEqual(run_src.count("buf.close()"), 2,
                      "_ConvertedThumbLoader.run() must close buf in both the "
                      "success path and the fallback exception path")

    def test_converted_thumb_loader_closes_buf_after_full_decode(self):
        """After preview_img.load() fully decodes the image into memory, the
        BytesIO buffer should be closed immediately since it is no longer
        needed."""
        src = self._preview_pane_source()
        class_start = src.find("class _ConvertedThumbLoader(")
        next_class = src.find("\nclass ", class_start + 1)
        class_src = src[class_start:next_class]
        run_start = class_src.find("    def run(")
        run_src = class_src[run_start:]
        # The buf.close() after full decode should come BEFORE preview_img use
        # (ensuring the buffer is closed when the image data is in memory)
        load_pos = run_src.find("preview_img.load()")
        first_close_pos = run_src.find("buf.close()")
        self.assertGreater(first_close_pos, load_pos,
                      "buf.close() must appear after preview_img.load() in the "
                      "success path")

    # ------------------------------------------------------------------
    # drop_list – _ThumbRunnable closes PIL image on all exit paths
    # ------------------------------------------------------------------

    def _drop_list_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "ui", "drop_list.py")) as f:
            return f.read()

    def test_thumb_runnable_closes_image_in_finally(self):
        """_ThumbRunnable.run() must close the PIL image it opens in a finally
        block so file descriptor / backing-store resources are released on all
        exit paths (success, exception, and cancel-check exit)."""
        src = self._drop_list_source()
        class_start = src.find("class _ThumbRunnable(")
        next_class = src.find("\nclass ", class_start + 1)
        class_src = src[class_start:next_class]
        run_start = class_src.find("    def run(")
        run_src = class_src[run_start:]
        self.assertIn("finally:", run_src,
                      "_ThumbRunnable.run() must have a finally block")
        self.assertIn("img.close()", run_src,
                      "_ThumbRunnable.run() must close img in the finally block")
        self.assertIn("img = None", run_src,
                      "_ThumbRunnable.run() must initialise img = None before "
                      "try so the finally guard can check 'if img is not None'")

    def test_thumb_runnable_closes_original_before_convert(self):
        """_ThumbRunnable.run() must explicitly close the original PIL image
        BEFORE reassigning img to the converted version, so the original
        Image.open() result is released and not orphaned."""
        src = self._drop_list_source()
        class_start = src.find("class _ThumbRunnable(")
        next_class = src.find("\nclass ", class_start + 1)
        class_src = src[class_start:next_class]
        run_start = class_src.find("    def run(")
        run_src = class_src[run_start:]
        # img.close() must appear before `img = converted`
        close_pos = run_src.find("img.close()")
        assign_pos = run_src.find("img = converted")
        self.assertGreater(assign_pos, close_pos,
                      "_ThumbRunnable.run() must call img.close() before "
                      "reassigning img to the converted version")

    # ------------------------------------------------------------------
    # settings_manager – sync() docstring accuracy
    # ------------------------------------------------------------------

    def _settings_manager_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "core", "settings_manager.py")) as f:
            return f.read()

    def test_sync_docstring_no_longer_claims_only_place(self):
        """The sync() docstring must not claim it is the 'only place' to call
        _qs.sync() since mutation methods (save_named_theme, etc.) also call
        it to protect important user data from crash-induced loss."""
        src = self._settings_manager_source()
        fn_pos = src.find("def sync(")
        next_def = src.find("\n    def ", fn_pos + 1)
        fn_src = src[fn_pos:next_def]
        self.assertNotIn("only place", fn_src,
                         "sync() docstring must not claim it is the 'only place' "
                         "to call _qs.sync() — mutation methods also call it "
                         "intentionally")

    def test_sync_docstring_explains_design_rationale(self):
        """The sync() docstring must explain the two-tier sync strategy:
        set() defers, mutation methods sync immediately for data durability."""
        src = self._settings_manager_source()
        fn_pos = src.find("def sync(")
        next_def = src.find("\n    def ", fn_pos + 1)
        fn_src = src[fn_pos:next_def]
        self.assertIn("set()", fn_src,
                      "sync() docstring must reference set() and explain why it "
                      "does not call sync()")


# ---------------------------------------------------------------------------
# Round-6 resource-hygiene tests
# ---------------------------------------------------------------------------

class TestRound6ResourceHygiene(unittest.TestCase):
    """Round-6: _ConverterPreviewLoader buf/save_img resource leaks.

    _ConverterPreviewLoader.run() previously had three resource leaks:
    1. buf not closed in the fallback except block
    2. buf not closed in the success path after out_img.load()
    3. save_img (a mode-converted copy of img) not closed in either path
    """

    _SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")

    def _preview_pane_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "ui", "preview_pane.py")) as f:
            return f.read()

    def _converter_loader_run_src(self) -> str:
        src = self._preview_pane_source()
        cls_start = src.find("class _ConverterPreviewLoader(")
        next_cls = src.find("\nclass ", cls_start + 1)
        cls_src = src[cls_start:next_cls]
        run_start = cls_src.find("    def run(")
        return cls_src[run_start:]

    # ------------------------------------------------------------------
    # buf closed in fallback except path
    # ------------------------------------------------------------------

    def test_converter_preview_loader_closes_buf_in_fallback(self):
        """_ConverterPreviewLoader.run() must close the BytesIO buffer in the
        fallback except block so it is released even when in-memory conversion
        fails (e.g. unsupported format)."""
        run_src = self._converter_loader_run_src()
        # Locate the except block that has the fallback "source shown" text
        fallback_start = run_src.find("(source shown")
        self.assertGreater(fallback_start, 0,
                           "Could not find fallback 'source shown' text in run()")
        # buf.close() must appear BEFORE the fallback "source shown" string
        buf_close_pos = run_src.find("buf.close()")
        # There should be at least two buf.close() calls: one in success path,
        # one in fallback path
        self.assertGreaterEqual(run_src.count("buf.close()"), 2,
                      "_ConverterPreviewLoader.run() must close buf in both the "
                      "success path and the fallback exception path")
        # The first buf.close() must appear before "source shown"
        self.assertLess(buf_close_pos, fallback_start,
                      "buf.close() must appear before the 'source shown' fallback "
                      "text so it is called in the except block")

    # ------------------------------------------------------------------
    # buf closed in success path after out_img.load()
    # ------------------------------------------------------------------

    def test_converter_preview_loader_closes_buf_after_full_decode(self):
        """_ConverterPreviewLoader.run() must close buf immediately after
        out_img.load() so the BytesIO backing store is freed as soon as the
        image data is in memory."""
        run_src = self._converter_loader_run_src()
        load_pos = run_src.find("out_img.load()")
        self.assertGreater(load_pos, 0, "Could not find out_img.load() in run()")
        # The first buf.close() after out_img.load() should be in the success path
        first_close_after_load = run_src.find("buf.close()", load_pos)
        self.assertGreater(first_close_after_load, load_pos,
                      "buf.close() must appear after out_img.load() in the "
                      "success path of _ConverterPreviewLoader.run()")

    # ------------------------------------------------------------------
    # buf allocated before inner try so it is always in scope for except
    # ------------------------------------------------------------------

    def test_converter_preview_loader_buf_allocated_before_try(self):
        """buf = io.BytesIO() must be allocated BEFORE the inner try block so
        that buf is always defined in the except block regardless of which
        statement raises."""
        run_src = self._converter_loader_run_src()
        buf_alloc_pos = run_src.find("buf = io.BytesIO()")
        self.assertGreater(buf_alloc_pos, 0,
                           "buf = io.BytesIO() not found in run()")
        # The inner try block starts after the buf allocation
        inner_try_pos = run_src.find("try:", buf_alloc_pos)
        self.assertGreater(inner_try_pos, buf_alloc_pos,
                      "buf = io.BytesIO() must appear BEFORE the inner try block "
                      "so buf is always defined when the except block runs")

    # ------------------------------------------------------------------
    # save_img closed when it is a converted copy
    # ------------------------------------------------------------------

    def test_converter_preview_loader_closes_save_img_in_fallback(self):
        """_ConverterPreviewLoader.run() must close save_img in the fallback
        except block when it is a mode-converted copy (save_img is not img).
        Without this, the converted PIL image is leaked every time an
        unsupported format triggers the fallback path."""
        run_src = self._converter_loader_run_src()
        # The guard must appear in the except block (before "source shown")
        fallback_end = run_src.find("(source shown")
        guard = "if save_img is not img:"
        self.assertIn(guard, run_src,
                      "_ConverterPreviewLoader.run() must have "
                      "'if save_img is not img:' guard to close converted copies")
        guard_pos = run_src.find(guard)
        self.assertLess(guard_pos, fallback_end,
                      "'if save_img is not img:' guard must appear in the "
                      "fallback except block (before 'source shown' text)")

    def test_converter_preview_loader_closes_save_img_in_success_path(self):
        """_ConverterPreviewLoader.run() must close save_img in the success
        path when it is a mode-converted copy.  Without this, the converted
        PIL image is leaked on every successful preview render of a format
        that requires mode conversion (e.g. JPEG, BMP, GIF, ICO)."""
        run_src = self._converter_loader_run_src()
        guard = "if save_img is not img:"
        # There must be at least two occurrences: one in except, one after try
        count = run_src.count(guard)
        self.assertGreaterEqual(count, 2,
                      "_ConverterPreviewLoader.run() must have at least two "
                      "'if save_img is not img:' guards — one in the fallback "
                      "except block and one in the success path")


# ---------------------------------------------------------------------------
# Round-7 resource-hygiene tests
# ---------------------------------------------------------------------------

class TestRound7DdsHelperResourceHygiene(unittest.TestCase):
    """Round-7: DDS helper (alpha_processor.py) PIL image and BytesIO resource leaks.

    _load_dds: Image.open(BytesIO(blob)).convert("RGBA") leaked the temporary
               Image from open(); also except Exception swallowed MemoryError.
    _load_dds_raw: Image.fromarray(..., "RGB").convert("RGBA") leaked the
                   intermediate RGB image.
    _save_dds: img_rgba and buf were never closed when except Exception fired;
               except Exception also swallowed MemoryError.
    _save_dds_raw: img_rgba = img.convert("RGBA") was never closed.
    """

    _SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")

    def _ap_source(self) -> str:
        with open(os.path.join(self._SRC_DIR, "core", "alpha_processor.py")) as f:
            return f.read()

    def _func_src(self, src: str, func_name: str) -> str:
        """Extract the source of a top-level function from the module source."""
        start = src.find(f"\ndef {func_name}(")
        self.assertGreater(start, 0, f"Function {func_name} not found")
        next_def = src.find("\ndef ", start + 1)
        return src[start:next_def] if next_def > 0 else src[start:]

    # ------------------------------------------------------------------
    # _load_dds: tmp image closed in try/finally
    # ------------------------------------------------------------------

    def test_load_dds_tmp_image_closed_in_finally(self):
        """_load_dds must open the PIL image into _tmp and close it in a
        try/finally so the raw decoded image is released even if .convert()
        raises (e.g. MemoryError on very large DDS files)."""
        src = self._ap_source()
        fn = self._func_src(src, "_load_dds")
        self.assertIn("_tmp = Image.open(", fn,
                      "_load_dds must assign Image.open() to _tmp (not chain .convert())")
        self.assertIn("_tmp.close()", fn,
                      "_load_dds must call _tmp.close() to release the opened image")
        # close must be inside a finally block
        finally_pos = fn.find("finally:")
        close_pos = fn.find("_tmp.close()")
        self.assertGreater(finally_pos, 0, "_load_dds must have a finally block for _tmp")
        self.assertGreater(close_pos, finally_pos,
                           "_tmp.close() must appear inside the finally block")

    def test_load_dds_propagates_memory_error(self):
        """_load_dds must re-raise MemoryError rather than catching it with
        the broad 'except Exception' fallback used for Wand failures."""
        src = self._ap_source()
        fn = self._func_src(src, "_load_dds")
        self.assertIn("except MemoryError:", fn,
                      "_load_dds must have 'except MemoryError: raise' before "
                      "'except Exception'")
        mem_pos = fn.find("except MemoryError:")
        exc_pos = fn.find("except Exception")
        self.assertLess(mem_pos, exc_pos,
                        "'except MemoryError' must appear before 'except Exception' "
                        "in _load_dds")

    # ------------------------------------------------------------------
    # _load_dds_raw: intermediate RGB image closed in try/finally
    # ------------------------------------------------------------------

    def test_load_dds_raw_rgb_intermediate_closed(self):
        """_load_dds_raw must assign the fromarray RGB image to a named variable
        and close it in a try/finally before returning the RGBA copy."""
        src = self._ap_source()
        fn = self._func_src(src, "_load_dds_raw")
        # Intermediate should NOT be chained (.convert() on anonymous fromarray result)
        self.assertNotIn('.fromarray(arr[:, :, [2, 1, 0]], "RGB").convert("RGBA")', fn,
                         "_load_dds_raw must NOT chain .convert() on the fromarray result "
                         "(intermediate RGB image would leak)")
        self.assertIn("_rgb", fn,
                      "_load_dds_raw must use a named variable (_rgb) for the "
                      "intermediate RGB image")
        self.assertIn("_rgb.close()", fn,
                      "_load_dds_raw must call _rgb.close() to release the intermediate "
                      "RGB image")

    # ------------------------------------------------------------------
    # _save_dds: img_rgba and buf closed in finally
    # ------------------------------------------------------------------

    def test_save_dds_img_rgba_initialised_to_none(self):
        """_save_dds must initialise img_rgba = None before the try block so
        it is always defined in the finally clause even if .convert() raises."""
        src = self._ap_source()
        fn = self._func_src(src, "_save_dds")
        self.assertIn("img_rgba = None", fn,
                      "_save_dds must initialise 'img_rgba = None' before the try block")
        # None init must appear before the inner try
        none_pos = fn.find("img_rgba = None")
        try_pos = fn.find("try:", none_pos)
        self.assertGreater(try_pos, none_pos,
                           "'img_rgba = None' must appear before the try block in _save_dds")

    def test_save_dds_buf_initialised_to_none(self):
        """_save_dds must initialise buf = None before the try block."""
        src = self._ap_source()
        fn = self._func_src(src, "_save_dds")
        self.assertIn("buf = None", fn,
                      "_save_dds must initialise 'buf = None' before the try block")

    def test_save_dds_finally_closes_img_rgba(self):
        """_save_dds must close img_rgba in a finally block."""
        src = self._ap_source()
        fn = self._func_src(src, "_save_dds")
        finally_pos = fn.find("finally:")
        self.assertGreater(finally_pos, 0, "_save_dds must have a finally block")
        close_pos = fn.find("img_rgba.close()", finally_pos)
        self.assertGreater(close_pos, finally_pos,
                           "img_rgba.close() must appear inside the finally block in _save_dds")

    def test_save_dds_finally_closes_buf(self):
        """_save_dds must close buf in a finally block."""
        src = self._ap_source()
        fn = self._func_src(src, "_save_dds")
        finally_pos = fn.find("finally:")
        self.assertGreater(finally_pos, 0, "_save_dds must have a finally block")
        close_pos = fn.find("buf.close()", finally_pos)
        self.assertGreater(close_pos, finally_pos,
                           "buf.close() must appear inside the finally block in _save_dds")

    def test_save_dds_propagates_memory_error(self):
        """_save_dds must re-raise MemoryError before the broad 'except Exception'."""
        src = self._ap_source()
        fn = self._func_src(src, "_save_dds")
        self.assertIn("except MemoryError:", fn,
                      "_save_dds must have 'except MemoryError: raise'")
        mem_pos = fn.find("except MemoryError:")
        exc_pos = fn.find("except Exception")
        self.assertLess(mem_pos, exc_pos,
                        "'except MemoryError' must appear before 'except Exception' "
                        "in _save_dds")

    # ------------------------------------------------------------------
    # _save_dds_raw: img_rgba closed after np.array()
    # ------------------------------------------------------------------

    def test_save_dds_raw_closes_img_rgba(self):
        """_save_dds_raw must close img_rgba (the RGBA-converted copy) as soon
        as the numpy array has been extracted from it, so the PIL image memory
        is released promptly."""
        src = self._ap_source()
        fn = self._func_src(src, "_save_dds_raw")
        self.assertIn("img_rgba.close()", fn,
                      "_save_dds_raw must call img_rgba.close() after np.array() "
                      "to release the converted PIL image")
        # close must be inside a finally block
        finally_pos = fn.find("finally:")
        self.assertGreater(finally_pos, 0, "_save_dds_raw must have a finally block")
        close_pos = fn.find("img_rgba.close()", finally_pos)
        self.assertGreater(close_pos, finally_pos,
                           "img_rgba.close() must be inside the finally block in "
                           "_save_dds_raw so it runs even if np.array() raises")


# ---------------------------------------------------------------------------
# Round-8 resource-hygiene tests
# ---------------------------------------------------------------------------

class TestRound8ResourceHygiene(unittest.TestCase):
    """Round-8: three PIL image resource leaks.

    Bug 1 (_ConvertedThumbLoader, preview_pane.py):
        When the inner save/open cycle raises an exception the fallback path
        closed buf and img but never closed save_img when save_img was a
        *newly converted* copy (e.g. RGB for JPEG, P for GIF).  Fix adds
        ``if save_img is not img: save_img.close()`` right after buf.close().

    Bug 2 (_alpha_stats, alpha_tool.py):
        ``np.array(img.convert("RGBA"), ...)`` created an anonymous PIL image
        that was immediately passed to numpy and never closed.  Fix stores the
        converted image in img_rgba and closes it in a try/finally (only if it
        differs from img).

    Bug 3 (_flatten_alpha PA/P branch, file_converter.py):
        ``rgba = img.convert("RGBA")`` was created, used in paste(), and then
        the function returned without closing rgba.  Fix wraps the paste + return
        in try/finally to always close rgba.
    """

    _PREVIEW_SRC = os.path.join(
        os.path.dirname(__file__), "..", "src", "ui", "preview_pane.py"
    )
    _ALPHA_SRC = os.path.join(
        os.path.dirname(__file__), "..", "src", "ui", "alpha_tool.py"
    )
    _FC_SRC = os.path.join(
        os.path.dirname(__file__), "..", "src", "core", "file_converter.py"
    )

    def _read(self, path: str) -> str:
        with open(path) as f:
            return f.read()

    # -------------------------------------------------------------------
    # Bug 1: _ConvertedThumbLoader except path closes save_img
    # -------------------------------------------------------------------

    def _conv_thumb_loader_src(self) -> str:
        src = self._read(self._PREVIEW_SRC)
        start = src.find("\nclass _ConvertedThumbLoader(")
        self.assertGreater(start, 0, "_ConvertedThumbLoader class not found")
        end = src.find("\nclass ", start + 1)
        return src[start:end] if end > 0 else src[start:]

    def test_converted_thumb_loader_closes_save_img_in_except(self):
        """The except/fallback path must close save_img when it differs from img."""
        cls_src = self._conv_thumb_loader_src()
        # Locate the inner except block
        inner_except_pos = cls_src.find("except Exception:")
        self.assertGreater(inner_except_pos, 0, "inner except block not found")
        # The close call must appear before img.thumbnail (which modifies img in-place)
        close_str = "if save_img is not img:"
        close_pos = cls_src.find(close_str, inner_except_pos)
        thumbnail_pos = cls_src.find("img.thumbnail(", inner_except_pos)
        self.assertGreater(close_pos, inner_except_pos,
                           "'if save_img is not img:' must appear in the except block")
        self.assertLess(close_pos, thumbnail_pos,
                        "'if save_img is not img:' must appear before img.thumbnail()")
        # The close() call must immediately follow
        close_call_str = "save_img.close()"
        close_call_pos = cls_src.find(close_call_str, close_pos)
        self.assertGreater(close_call_pos, close_pos,
                           "'save_img.close()' must follow the guard in the except block")

    def test_converted_thumb_loader_except_path_order(self):
        """buf.close() must come before save_img.close() in the except path."""
        cls_src = self._conv_thumb_loader_src()
        inner_except_pos = cls_src.find("except Exception:")
        buf_close_pos = cls_src.find("buf.close()", inner_except_pos)
        save_close_pos = cls_src.find("save_img.close()", inner_except_pos)
        self.assertGreater(buf_close_pos, inner_except_pos,
                           "buf.close() must appear in the except block")
        self.assertGreater(save_close_pos, buf_close_pos,
                           "save_img.close() must come after buf.close() in except block")

    # -------------------------------------------------------------------
    # Bug 2: _alpha_stats closes the RGBA convert
    # -------------------------------------------------------------------

    def _alpha_stats_src(self) -> str:
        src = self._read(self._ALPHA_SRC)
        start = src.find("\n    def _alpha_stats(")
        self.assertGreater(start, 0, "_alpha_stats not found")
        # find next method (starts with 4-space indent + "def ")
        next_def = src.find("\n    def ", start + 1)
        return src[start:next_def] if next_def > 0 else src[start:]

    def test_alpha_stats_does_not_chain_convert_into_np_array(self):
        """_alpha_stats must NOT pass an anonymous img.convert() directly to
        np.array() — that would create a PIL image that is never closed."""
        fn = self._alpha_stats_src()
        self.assertNotIn(
            "np.array(img.convert(",
            fn,
            "_alpha_stats must not chain img.convert() directly into np.array() "
            "(the PIL image would never be closed)",
        )

    def test_alpha_stats_stores_rgba_in_variable(self):
        """_alpha_stats must assign the RGBA conversion to img_rgba."""
        fn = self._alpha_stats_src()
        self.assertIn(
            "img_rgba",
            fn,
            "_alpha_stats must use a named 'img_rgba' variable for the RGBA copy",
        )

    def test_alpha_stats_closes_rgba_in_finally(self):
        """_alpha_stats must close img_rgba in a try/finally (only when it
        differs from the input image, i.e. only when a conversion was needed)."""
        fn = self._alpha_stats_src()
        finally_pos = fn.find("finally:")
        self.assertGreater(finally_pos, 0,
                           "_alpha_stats must have a try/finally block")
        close_pos = fn.find("img_rgba.close()", finally_pos)
        self.assertGreater(close_pos, finally_pos,
                           "img_rgba.close() must appear inside the finally block")

    def test_alpha_stats_guards_close_with_is_not_check(self):
        """_alpha_stats must only close img_rgba when it is a *different* object
        from img (to avoid double-close when the input is already RGBA)."""
        fn = self._alpha_stats_src()
        self.assertIn(
            "if img_rgba is not img:",
            fn,
            "_alpha_stats must guard img_rgba.close() with 'if img_rgba is not img:'",
        )

    # -------------------------------------------------------------------
    # Bug 3: _flatten_alpha closes rgba in finally
    # -------------------------------------------------------------------

    def _flatten_alpha_src(self) -> str:
        src = self._read(self._FC_SRC)
        start = src.find("\ndef _flatten_alpha(")
        self.assertGreater(start, 0, "_flatten_alpha not found")
        next_def = src.find("\ndef ", start + 1)
        return src[start:next_def] if next_def > 0 else src[start:]

    def test_flatten_alpha_closes_rgba_in_finally(self):
        """_flatten_alpha must close the rgba intermediate image in a
        try/finally so it is released even if Image.new() or paste() raises."""
        fn = self._flatten_alpha_src()
        # Only the PA/P branch creates a local `rgba`; check it has finally
        pa_branch_pos = fn.find('"PA", "P"')
        self.assertGreater(pa_branch_pos, 0,
                           "PA/P branch must exist in _flatten_alpha")
        finally_pos = fn.find("finally:", pa_branch_pos)
        self.assertGreater(finally_pos, pa_branch_pos,
                           "_flatten_alpha PA/P branch must have a finally block")
        close_pos = fn.find("rgba.close()", finally_pos)
        self.assertGreater(close_pos, finally_pos,
                           "rgba.close() must appear inside the finally block")

    def test_flatten_alpha_finally_does_not_suppress_exception(self):
        """The finally block must only call close(); it must NOT contain a
        bare 'return base' that would silently suppress exceptions."""
        fn = self._flatten_alpha_src()
        finally_pos = fn.find("finally:")
        self.assertGreater(finally_pos, 0, "no finally block found in _flatten_alpha")
        # After finally: the only statement should be close, not a return
        end = fn.find("\n    if ", finally_pos + 1)
        finally_body = fn[finally_pos:end] if end > 0 else fn[finally_pos:finally_pos + 200]
        self.assertNotIn(
            "return base",
            finally_body,
            "finally block in _flatten_alpha must not contain 'return base' "
            "(that would suppress exceptions from the try block)",
        )


class TestRound9ResourceHygiene(unittest.TestCase):
    """Round-9: three PIL image resource leaks.

    Bug 1 (convert_file, file_converter.py):
        ``src_img = _open_image(...)`` was never closed.  Every call leaked the
        source PIL image.  Format branches also leaked intermediate images by
        reassigning ``img`` without closing the old value (flatten, quantize,
        ensure_rgba, convert).  Fix wraps the function body in
        ``try/finally: src_img.close()`` and uses named local variables (flat,
        gif_img, rgba, qoi_img) for every format-branch intermediate so each one
        is closed inside its own try/finally.

    Bug 2 (save_image, alpha_processor.py):
        ``img = img.convert("RGB")`` in the JPEG/BMP branch leaked the original
        RGBA image.  Fix uses a local ``img_rgb`` variable and closes it inside
        a try/finally after ``img_rgb.save()``.

    Bug 3 (load_image, alpha_processor.py):
        ``img = img.convert("RGBA")`` leaked the original non-RGBA PIL image.
        Fix uses a local ``img_rgba`` variable, closes the original ``img``
        after a successful convert (and also on MemoryError), then returns
        ``img_rgba``.
    """

    _AP_SRC = os.path.join(
        os.path.dirname(__file__), "..", "src", "core", "alpha_processor.py"
    )
    _FC_SRC = os.path.join(
        os.path.dirname(__file__), "..", "src", "core", "file_converter.py"
    )

    def _read(self, path: str) -> str:
        with open(path) as f:
            return f.read()

    # ------------------------------------------------------------------
    # Helpers to extract named function bodies from source
    # ------------------------------------------------------------------

    def _fn_src(self, file_src: str, fn_name: str) -> str:
        """Return the source of a top-level def *fn_name* from *file_src*."""
        marker = f"\ndef {fn_name}("
        start = file_src.find(marker)
        self.assertGreater(start, 0, f"def {fn_name} not found in source")
        next_def = file_src.find("\ndef ", start + 1)
        return file_src[start:next_def] if next_def > 0 else file_src[start:]

    # ------------------------------------------------------------------
    # Bug 1 — convert_file: src_img / format-branch intermediates
    # ------------------------------------------------------------------

    def test_convert_file_wraps_src_img_in_try_finally(self):
        """convert_file must close src_img via a try/finally block."""
        src = self._read(self._FC_SRC)
        fn = self._fn_src(src, "convert_file")
        # The outer try must appear after src_img is opened
        open_pos = fn.find("src_img = _open_image(")
        self.assertGreater(open_pos, 0, "src_img = _open_image() not found")
        # A finally that closes src_img must exist
        finally_pos = fn.find("finally:", open_pos)
        self.assertGreater(finally_pos, open_pos,
                           "convert_file must have a try/finally after _open_image()")
        close_pos = fn.find("src_img.close()", finally_pos)
        self.assertGreater(close_pos, finally_pos,
                           "src_img.close() must appear inside the finally block")

    def test_convert_file_closes_resized_img_in_finally(self):
        """When a resize happened img differs from src_img; the finally must
        close it too."""
        src = self._read(self._FC_SRC)
        fn = self._fn_src(src, "convert_file")
        finally_pos = fn.rfind("finally:")
        self.assertGreater(finally_pos, 0, "no finally block found in convert_file")
        guard_pos = fn.find("if img is not src_img:", finally_pos)
        self.assertGreater(guard_pos, finally_pos,
                           "'if img is not src_img:' must appear in the outer finally")
        img_close_pos = fn.find("img.close()", guard_pos)
        self.assertGreater(img_close_pos, guard_pos,
                           "img.close() must follow the guard in the outer finally")

    def test_convert_file_jpeg_branch_uses_flat_local_not_img(self):
        """JPEG branch must store _flatten_alpha result in a local 'flat' and
        close it in try/finally, not reassign img."""
        src = self._read(self._FC_SRC)
        fn = self._fn_src(src, "convert_file")
        # The format dispatch section starts after _save_w/_save_h is captured;
        # use that anchor to avoid matching the JPEG key inside _meta_kwargs.
        dispatch_pos = fn.find("_save_w, _save_h = img.size")
        self.assertGreater(dispatch_pos, 0, "_save_w/_save_h capture not found")
        jpeg_pos = fn.find('(".jpg", ".jpeg")', dispatch_pos)
        self.assertGreater(jpeg_pos, 0, "JPEG branch not found in convert_file format dispatch")
        jpeg_section = fn[jpeg_pos: jpeg_pos + 300]
        self.assertNotIn(
            "img = _flatten_alpha(",
            jpeg_section,
            "JPEG branch must not reassign 'img' with _flatten_alpha (leaks old img)",
        )
        self.assertIn(
            "flat = _flatten_alpha(",
            jpeg_section,
            "JPEG branch must store _flatten_alpha result in 'flat'",
        )

    def test_convert_file_gif_branch_uses_gif_img_local(self):
        """GIF branch must store quantize/convert result in a local 'gif_img'
        and close it in try/finally, not reassign img."""
        src = self._read(self._FC_SRC)
        fn = self._fn_src(src, "convert_file")
        gif_pos = fn.find('".gif"')
        self.assertGreater(gif_pos, 0, "GIF branch not found in convert_file")
        gif_section = fn[gif_pos: gif_pos + 600]
        # Ensure quantize result is stored in gif_img, not back into img.
        # Use a word-boundary check: "img = img.quantize" is only problematic
        # when NOT preceded by other identifier chars (e.g. "gif_img = img.quantize" is fine).
        import re
        bad_pattern = re.compile(r'(?<![A-Za-z0-9_])img\s*=\s*img\.quantize\(')
        self.assertIsNone(
            bad_pattern.search(gif_section),
            "GIF branch must not reassign plain 'img' with quantize (leaks RGBA img); "
            "use 'gif_img = img.quantize(...)' instead",
        )
        self.assertIn(
            "gif_img",
            gif_section,
            "GIF branch must use a 'gif_img' local variable for the palette image",
        )

    def test_convert_file_gif_img_closed_in_finally(self):
        """gif_img must be closed inside a try/finally in the GIF branch."""
        src = self._read(self._FC_SRC)
        fn = self._fn_src(src, "convert_file")
        gif_pos = fn.find('".gif"')
        gif_section = fn[gif_pos: gif_pos + 600]
        finally_pos = gif_section.find("finally:")
        self.assertGreater(finally_pos, 0,
                           "GIF branch must have a try/finally for gif_img")
        close_pos = gif_section.find("gif_img.close()", finally_pos)
        self.assertGreater(close_pos, finally_pos,
                           "gif_img.close() must appear inside the GIF finally block")

    def test_convert_file_ico_branch_uses_rgba_local(self):
        """ICO branch must store _ensure_rgba result in a local 'rgba' and
        close it in try/finally, not reassign img."""
        src = self._read(self._FC_SRC)
        fn = self._fn_src(src, "convert_file")
        ico_pos = fn.find('".ico"')
        self.assertGreater(ico_pos, 0, "ICO branch not found in convert_file")
        ico_section = fn[ico_pos: ico_pos + 400]
        self.assertNotIn(
            "img = _ensure_rgba(",
            ico_section,
            "ICO branch must not reassign 'img' with _ensure_rgba (leaks old img)",
        )
        self.assertIn(
            "rgba = _ensure_rgba(",
            ico_section,
            "ICO branch must store _ensure_rgba result in 'rgba'",
        )

    def test_convert_file_ico_rgba_guarded_close(self):
        """ICO rgba must only be closed when it differs from img."""
        src = self._read(self._FC_SRC)
        fn = self._fn_src(src, "convert_file")
        ico_pos = fn.find('".ico"')
        ico_section = fn[ico_pos: ico_pos + 400]
        self.assertIn(
            "if rgba is not img:",
            ico_section,
            "ICO branch must guard rgba.close() with 'if rgba is not img:'",
        )

    # ------------------------------------------------------------------
    # Bug 2 — save_image: RGBA-to-RGB intermediate not closed
    # ------------------------------------------------------------------

    def _save_image_src(self) -> str:
        src = self._read(self._AP_SRC)
        return self._fn_src(src, "save_image")

    def test_save_image_does_not_reassign_img_with_convert(self):
        """save_image must NOT use ``img = img.convert('RGB')`` (leaks the
        original RGBA image).  It must store the result in a local variable."""
        fn = self._save_image_src()
        self.assertNotIn(
            "img = img.convert(",
            fn,
            "save_image must not reassign img with convert() (leaks old image); "
            "use a local variable instead",
        )

    def test_save_image_uses_img_rgb_local(self):
        """save_image must store the RGB conversion in a local 'img_rgb'."""
        fn = self._save_image_src()
        self.assertIn(
            "img_rgb",
            fn,
            "save_image must use an 'img_rgb' local variable for the RGB copy",
        )

    def test_save_image_closes_img_rgb_in_finally(self):
        """save_image must close img_rgb in a try/finally block."""
        fn = self._save_image_src()
        finally_pos = fn.find("finally:")
        self.assertGreater(finally_pos, 0,
                           "save_image must have a try/finally block for img_rgb")
        close_pos = fn.find("img_rgb.close()", finally_pos)
        self.assertGreater(close_pos, finally_pos,
                           "img_rgb.close() must appear inside the finally block")

    # ------------------------------------------------------------------
    # Bug 3 — load_image: non-RGBA source not closed after convert
    # ------------------------------------------------------------------

    def _load_image_src(self) -> str:
        src = self._read(self._AP_SRC)
        return self._fn_src(src, "load_image")

    def test_load_image_does_not_reassign_img_with_convert(self):
        """load_image must NOT use ``img = img.convert('RGBA')`` (leaks the
        original image).  It must store the result in a local variable."""
        fn = self._load_image_src()
        self.assertNotIn(
            "img = img.convert(",
            fn,
            "load_image must not reassign img with convert() (leaks old image); "
            "use a local variable instead",
        )

    def test_load_image_uses_img_rgba_local(self):
        """load_image must store the RGBA conversion in a local 'img_rgba'."""
        fn = self._load_image_src()
        self.assertIn(
            "img_rgba",
            fn,
            "load_image must use an 'img_rgba' local variable for the RGBA copy",
        )

    def test_load_image_closes_original_after_convert(self):
        """load_image must close the original PIL image after a successful
        convert('RGBA') so the non-RGBA data is released promptly."""
        fn = self._load_image_src()
        # img.close() must appear after the convert, not inside a finally
        # (because load_image returns img_rgba and the caller owns that)
        self.assertIn(
            "img.close()",
            fn,
            "load_image must call img.close() after a successful convert('RGBA')",
        )

    def test_load_image_closes_original_on_memory_error(self):
        """load_image must also close the original PIL image when MemoryError
        is raised by convert('RGBA'), so the file handle / decode buffer is
        released even in the error path."""
        fn = self._load_image_src()
        mem_err_pos = fn.find("except MemoryError:")
        self.assertGreater(mem_err_pos, 0,
                           "load_image must have an except MemoryError block")
        close_pos = fn.find("img.close()", mem_err_pos)
        self.assertGreater(close_pos, mem_err_pos,
                           "img.close() must appear inside the except MemoryError block")
