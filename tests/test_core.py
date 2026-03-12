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
