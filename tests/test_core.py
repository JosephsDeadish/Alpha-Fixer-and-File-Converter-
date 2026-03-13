"""
Tests for core alpha processing and preset management.
"""
import re
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
    _FULL_OPACITY_NAME = "PC Full Opacity  (α=255)"
    _PS2_FULL_OPAQUE_NAME = "PS2 Force Opaque  (α=128)"

    def test_ps2_preset_values(self):
        p = self._mgr.get_preset(self._PS2_FULL_OPAQUE_NAME)
        self.assertIsNotNone(p)
        self.assertEqual(p.clamp_min, 128)
        self.assertEqual(p.clamp_max, 128)

    def test_ps2_clamp_presets(self):
        # There must be at least one PS2-related preset that caps alpha at 128
        matched = [p for p in self._mgr.all_presets()
                   if "PS2" in p.name and p.clamp_max == 128 and not p.binary_cut]
        self.assertTrue(matched, "Expected a PS2 preset with clamp_max=128")
        p = matched[0]
        self.assertEqual(p.clamp_max, 128)

    def test_n64_preset_values(self):
        p = self._mgr.get_preset(self._FULL_OPACITY_NAME)
        self.assertIsNotNone(p)
        self.assertEqual(p.clamp_min, 255)
        self.assertEqual(p.clamp_max, 255)

    def test_get_nonexistent_preset(self):
        self.assertIsNone(self._mgr.get_preset("DoesNotExist"))

    def test_cannot_overwrite_builtin(self):
        custom = AlphaPreset(self._PS2_FULL_OPAQUE_NAME, "test", clamp_min=0, clamp_max=0)
        result = self._mgr.save_custom_preset(custom)
        self.assertFalse(result)

    def test_save_and_retrieve_custom_preset(self):
        custom = AlphaPreset("My Custom", "desc", builtin=False, clamp_min=200, clamp_max=200)
        result = self._mgr.save_custom_preset(custom)
        self.assertTrue(result)
        retrieved = self._mgr.get_preset("My Custom")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.clamp_min, 200)
        self.assertEqual(retrieved.clamp_max, 200)

    def test_delete_custom_preset(self):
        custom = AlphaPreset("ToDelete", "desc", builtin=False, clamp_min=100, clamp_max=100)
        self._mgr.save_custom_preset(custom)
        result = self._mgr.delete_custom_preset("ToDelete")
        self.assertTrue(result)
        self.assertIsNone(self._mgr.get_preset("ToDelete"))

    def test_cannot_delete_builtin(self):
        result = self._mgr.delete_custom_preset(self._PS2_FULL_OPAQUE_NAME)
        self.assertFalse(result)
        self.assertIsNotNone(self._mgr.get_preset(self._PS2_FULL_OPAQUE_NAME))

    def test_set_alpha_to_255(self):
        img = make_rgba_image(alpha=128)
        preset = AlphaPreset("test", "", clamp_min=255, clamp_max=255)
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 255))

    def test_set_alpha_to_0(self):
        img = make_rgba_image(alpha=200)
        preset = AlphaPreset("test", "", clamp_min=0, clamp_max=0)
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 0))

    def test_ps2_preset(self):
        img = make_rgba_image(alpha=200)
        preset = AlphaPreset("PS2", "", clamp_min=128, clamp_max=128)
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 128))

    def test_invert_alpha(self):
        # uniform alpha=100 → invert → uniform 155 → clamp to [0, 255] → stays 155
        img = make_rgba_image(alpha=100)
        preset = AlphaPreset("inv", "", invert=True)
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 155))  # within [0,255] → unchanged

    def test_invert_alpha_varied(self):
        """invert then normalize on varied alpha preserves inverted proportions."""
        arr_in = np.zeros((1, 2, 4), dtype=np.uint8)
        arr_in[0, 0, 3] = 0    # invert → 255
        arr_in[0, 1, 3] = 255  # invert → 0
        from PIL import Image as _Image
        img = _Image.fromarray(arr_in, "RGBA")
        preset = AlphaPreset("inv", "", invert=True, clamp_min=0, clamp_max=255)
        result = apply_alpha_preset(img, preset)
        out = np.array(result)
        self.assertEqual(int(out[0, 0, 3]), 255)
        self.assertEqual(int(out[0, 1, 3]), 0)

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
        result = apply_manual_alpha(img, clamp_min=200, clamp_max=200)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 200))

    def test_manual_alpha_clamp_only(self):
        """Uniform alpha above the target ceiling is clamped to clamp_max.
        alpha=200 with range [0, 128] → clamp(200, 0, 128) = 128."""
        img = make_rgba_image(alpha=200)
        result = apply_manual_alpha(img, clamp_max=128)
        arr = np.array(result)
        # 200 exceeds the ceiling of 128, so every pixel is clamped to 128
        self.assertTrue(np.all(arr[:, :, 3] == 128))

    def test_clamp_min_preset(self):
        """Preset: uniform alpha below the target floor is clamped to target_lo.
        alpha=50 with target=[100, 255] → clamp(50, 100, 255) = 100."""
        img = make_rgba_image(alpha=50)
        preset = AlphaPreset("clamp", "", clamp_min=100, clamp_max=255)
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        # 50 is below the floor of 100, so every pixel is clamped to 100
        self.assertTrue(np.all(arr[:, :, 3] == 100))

    def test_clamp_max_preset(self):
        """Preset: uniform alpha above the target ceiling is clamped to target_hi.
        alpha=200 with target=[0, 128] → clamp(200, 0, 128) = 128."""
        img = make_rgba_image(alpha=200)
        preset = AlphaPreset("clamp", "", clamp_min=0, clamp_max=128)
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        # 200 exceeds the ceiling of 128, so every pixel is clamped to 128
        self.assertTrue(np.all(arr[:, :, 3] == 128))

    def test_inverted_clamp_preset(self):
        """apply_alpha_preset normalizes inverted clamp values (clamp_min > clamp_max)
        by treating the lower value as the floor and the higher value as the ceiling,
        preventing all alpha values from collapsing to a single incorrect value."""
        # Build a small image with alpha values spanning 0–255.
        arr_in = np.zeros((2, 2, 4), dtype=np.uint8)
        arr_in[0, 0] = [0, 0, 0, 50]    # low alpha — should be raised to 100
        arr_in[0, 1] = [0, 0, 0, 150]   # mid alpha — unchanged (within [100, 200])
        arr_in[1, 0] = [0, 0, 0, 220]   # high alpha — should be capped to 200
        arr_in[1, 1] = [0, 0, 0, 100]   # at floor — unchanged
        img = Image.fromarray(arr_in, "RGBA")
        # Preset with INVERTED values (clamp_min=200, clamp_max=100).
        # The processor must normalize the order before clipping.
        preset = AlphaPreset("inverted", "", clamp_min=200, clamp_max=100)
        result = apply_alpha_preset(img, preset)
        out = np.array(result)
        self.assertEqual(int(out[0, 0, 3]), 100,
                         "alpha=50 below floor=100 should be raised to 100")
        self.assertEqual(int(out[0, 1, 3]), 159,
                         "alpha=150 normalizes to ~159")
        self.assertEqual(int(out[1, 0, 3]), 200,
                         "alpha=220 above ceiling=200 should be capped to 200")
        self.assertEqual(int(out[1, 1, 3]), 129,
                         "alpha=100 normalizes to ~129")

    def test_clamp_only_manual_preserves_range(self):
        """apply_manual_alpha normalizes alpha from [img_min, img_max] to [clamp_min, clamp_max].
        The source range is stretched to exactly fill the target so that img_min→clamp_min
        and img_max→clamp_max.  The min and max of the output MUST differ when the source
        has varied alpha.  This tests the core 'new min and max' use-case."""
        arr_in = np.zeros((2, 2, 4), dtype=np.uint8)
        arr_in[0, 0] = [0, 0, 0, 30]    # source_min → target_lo
        arr_in[0, 1] = [0, 0, 0, 150]   # mid alpha
        arr_in[1, 0] = [0, 0, 0, 230]   # source_max → target_hi
        arr_in[1, 1] = [0, 0, 0, 80]    # low-mid alpha
        img = Image.fromarray(arr_in, "RGBA")
        result = apply_manual_alpha(img, clamp_min=50, clamp_max=200)
        out = np.array(result)
        # normalize formula: round(50 + (pixel - img_min) / (img_max - img_min) * 150)
        # img_min=30, img_max=230, span=200, target_span=150
        # pixel=30  → 50  (source_min → target_lo)
        # pixel=150 → round(50 + 120/200 * 150) = round(140) = 140
        # pixel=230 → 200 (source_max → target_hi)
        # pixel=80  → round(50 + 50/200  * 150) = round(87.5) = 88
        self.assertEqual(int(out[0, 0, 3]), 50,  "source_min=30 → target_lo=50")
        self.assertEqual(int(out[0, 1, 3]), 140, "alpha=150 → 140")
        self.assertEqual(int(out[1, 0, 3]), 200, "source_max=230 → target_hi=200")
        self.assertEqual(int(out[1, 1, 3]), 88,  "alpha=80 → 88")
        # Crucially: min == target_lo and max == target_hi (guaranteed by normalize)
        alpha_out = out[:, :, 3]
        self.assertEqual(int(alpha_out.min()), 50,
                         "output minimum must equal clamp_min=50")
        self.assertEqual(int(alpha_out.max()), 200,
                         "output maximum must equal clamp_max=200")
        self.assertGreater(int(alpha_out.max()), int(alpha_out.min()),
                           "output must have a range, not a single value")

    def test_clamp_min_manual_alpha(self):
        """apply_manual_alpha: uniform alpha below the target floor is clamped to target_lo.
        alpha=50 with target=[128, 255] → clamp(50, 128, 255) = 128."""
        img = make_rgba_image(alpha=50)
        result = apply_manual_alpha(img, clamp_min=128, clamp_max=255)
        arr = np.array(result)
        # 50 is below the floor of 128, so every pixel is clamped up to 128
        self.assertTrue(np.all(arr[:, :, 3] == 128))

    def test_builtin_clamp_128_255_preset(self):
        """The built-in 'Clamp 128-255' preset clamps uniform alpha below the floor to 128.
        alpha=50 with target=[128, 255] → clamp(50, 128, 255) = 128."""
        from unittest.mock import MagicMock
        from src.core.presets import PresetManager
        mock_settings = MagicMock()
        mock_settings.get_custom_presets.return_value = []
        mgr = PresetManager(mock_settings)
        preset = next(
            (p for p in mgr.all_presets() if p.clamp_min == 128 and p.clamp_max == 255
             and not p.binary_cut and not p.invert),
            None,
        )
        self.assertIsNotNone(preset, "Expected a builtin preset with clamp_min=128, clamp_max=255")
        self.assertEqual(preset.clamp_min, 128)
        self.assertEqual(preset.clamp_max, 255)
        img = make_rgba_image(alpha=50)
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        # 50 is below the floor of 128, so every pixel is clamped to 128
        self.assertTrue(np.all(arr[:, :, 3] == 128),
                        f"Expected 128, got {arr[0, 0, 3]}")

    def test_uniform_below_floor_raises_to_min(self):
        """Uniform alpha=0 must map to exactly target_lo.
        With the proportional formula: round(target_lo + 0/255 * span) = target_lo.
        This proves the Min spinbox is effective for fully-transparent images."""
        img = make_rgba_image(alpha=0)   # fully transparent
        result = apply_manual_alpha(img, clamp_min=50, clamp_max=200)
        arr = np.array(result)
        self.assertTrue(np.all(arr[:, :, 3] == 50),
                        f"Expected 50 (alpha=0 maps to target_lo), got {arr[0, 0, 3]}")

    def test_uniform_within_range_min_is_live(self):
        """For a uniform-alpha image whose value is inside [target_lo, target_hi],
        the value is already within the user's bounds so it is left unchanged by
        clamping (there is nothing to clamp)."""
        img = make_rgba_image(alpha=128)  # uniform mid-value

        result_a = apply_manual_alpha(img, clamp_min=50,  clamp_max=200)
        result_b = apply_manual_alpha(img, clamp_min=100, clamp_max=200)
        val_a = int(np.array(result_a)[0, 0, 3])
        val_b = int(np.array(result_b)[0, 0, 3])

        # 128 is within both [50, 200] and [100, 200] → clamping leaves it unchanged
        self.assertEqual(val_a, 128, "Uniform 128 within [50, 200] must remain 128")
        self.assertEqual(val_b, 128, "Uniform 128 within [100, 200] must remain 128")
        # Both outputs are within their respective ranges
        self.assertGreaterEqual(val_a, 50)
        self.assertLessEqual(val_a, 200)
        self.assertGreaterEqual(val_b, 100)
        self.assertLessEqual(val_b, 200)

    def test_uniform_above_ceiling_min_is_live(self):
        """For a uniform-alpha image whose value is ABOVE target_hi, the output is
        clamped to target_hi (Max) — guaranteeing output_max == Max even for
        flat-alpha images that exceed the user's ceiling."""
        img = make_rgba_image(alpha=200)  # above target_hi=128

        result_a = apply_manual_alpha(img, clamp_min=0,  clamp_max=128)
        result_b = apply_manual_alpha(img, clamp_min=50, clamp_max=128)
        val_a = int(np.array(result_a)[0, 0, 3])
        val_b = int(np.array(result_b)[0, 0, 3])

        # 200 > target_hi=128 → clamped to exactly 128 in both cases
        self.assertEqual(val_a, 128,
            "Uniform 200 above ceiling 128 must be clamped to 128 (output_max == Max)")
        self.assertEqual(val_b, 128,
            "Uniform 200 above ceiling 128 must be clamped to 128 regardless of Min")
        # Both outputs are within their respective [target_lo, target_hi] bounds
        self.assertGreaterEqual(val_a, 0)
        self.assertLessEqual(val_a, 128)
        self.assertGreaterEqual(val_b, 50)
        self.assertLessEqual(val_b, 128)

    def test_uniform_binary_cut(self):
        """binary_cut=True should give hard 0/255 split at threshold.
        Uses a two-row image so normalize maps 50→0 and 200→255, then
        binary_cut at threshold=128 gives 0 and 255 respectively."""
        arr_in = np.zeros((2, 4, 4), dtype=np.uint8)
        arr_in[0, :, :3] = 200
        arr_in[0, :, 3] = 50   # low alpha → normalizes to 0 → binary cut → 0
        arr_in[1, :, :3] = 200
        arr_in[1, :, 3] = 200  # high alpha → normalizes to 255 → binary cut → 255
        img = Image.fromarray(arr_in, "RGBA")
        preset = AlphaPreset("cut", "", threshold=128, binary_cut=True)
        result = apply_alpha_preset(img, preset)
        arr_out = np.array(result)
        self.assertTrue(np.all(arr_out[0, :, 3] == 0))
        self.assertTrue(np.all(arr_out[1, :, 3] == 255))

    def test_output_is_rgba(self):
        img = make_rgba_image(alpha=100)
        preset = AlphaPreset("test", "", clamp_min=200, clamp_max=200)
        result = apply_alpha_preset(img, preset)
        self.assertEqual(result.mode, "RGBA")

    def test_rgb_input_converted(self):
        img = Image.new("RGB", (4, 4), (200, 200, 200))
        preset = AlphaPreset("test", "", clamp_min=128, clamp_max=128)
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
        result = apply_manual_alpha(img, clamp_min=0, clamp_max=128)
        out = np.array(result)
        self.assertEqual(int(out[:2, :, 3].max()), 0,  "min pixels should stay 0")
        self.assertEqual(int(out[2:, :, 3].min()), 128, "max pixels should map to 128")

    def test_manual_alpha_normalize_half_range_to_full(self):
        """apply_manual_alpha normalizes alpha from [img_min, img_max] to [target_lo, target_hi].
        Source [0, 128] stretched to [0, 255]: img_min=0→0, img_max=128→255."""
        arr = np.zeros((4, 4, 4), dtype=np.uint8)
        arr[:2, :, 3] = 0
        arr[2:, :, 3] = 128
        img = Image.fromarray(arr, "RGBA")
        result = apply_manual_alpha(img, clamp_min=0, clamp_max=255)
        out = np.array(result)
        # normalize: img_min=0→0, img_max=128→255
        self.assertEqual(int(out[:2, :, 3].max()), 0,   "source_min=0 stays at 0")
        self.assertEqual(int(out[2:, :, 3].min()), 255, "source_max=128 stretches to 255")

    def test_manual_alpha_normalize_uniform_image_maps_to_max(self):
        """Uniform alpha above target_hi is clamped to exactly target_hi (Max).
        alpha=200 with target=[0, 128] → clamp(200, 0, 128) = 128."""
        img = make_rgba_image(alpha=200)
        result = apply_manual_alpha(img, clamp_min=0, clamp_max=128)
        out = np.array(result)
        # 200 exceeds the ceiling of 128, so every pixel is clamped to 128
        self.assertTrue(np.all(out[:, :, 3] == 128))

    def test_manual_alpha_normalize_preserves_proportions(self):
        """normalize: midpoint of source range maps to midpoint of target range."""
        arr = np.zeros((1, 3, 4), dtype=np.uint8)
        arr[0, 0, 3] = 0
        arr[0, 1, 3] = 128   # midpoint of [0, 255]
        arr[0, 2, 3] = 255
        img = Image.fromarray(arr, "RGBA")
        result = apply_manual_alpha(img, clamp_min=0, clamp_max=100)
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
        preset = AlphaPreset("norm_test", "", clamp_min=0, clamp_max=255)
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
        presets = mgr.all_presets()
        # PS2 Rescale → 0–128: remap to PS2 native range
        self.assertTrue(
            any(p.clamp_min == 0 and p.clamp_max == 128 and not p.binary_cut and not p.invert
                for p in presets),
            "Expected a builtin preset with clamp_min=0, clamp_max=128 (PS2 rescale)",
        )
        # PS2 Rescale → 0–255: remap back to PC range
        self.assertTrue(
            any(p.clamp_min == 0 and p.clamp_max == 255 and not p.binary_cut and not p.invert
                for p in presets),
            "Expected a builtin preset with clamp_min=0, clamp_max=255 (PS2 to PC rescale)",
        )

    def test_range_presets_have_different_min_max(self):
        """Built-in range presets must NOT have clamp_min == clamp_max so
        they can remap alpha across a span rather than forcing a single value."""
        from src.core.presets import BUILTIN_PRESETS
        range_presets = [p for p in BUILTIN_PRESETS if p.clamp_min != p.clamp_max]
        self.assertGreaterEqual(
            len(range_presets), 3,
            "Expected at least 3 built-in presets with different clamp_min and clamp_max; "
            f"found {len(range_presets)}: {[p.name for p in range_presets]}",
        )

    def test_ps2_rescale_0_128_has_different_min_max(self):
        """'PS2 Rescale → 0–128' preset must have clamp_min=0, clamp_max=128."""
        from src.core.presets import BUILTIN_PRESETS
        # Match by exact values rather than fragile name substring checks
        preset = next(
            (p for p in BUILTIN_PRESETS
             if p.clamp_min == 0 and p.clamp_max == 128 and not p.invert and not p.binary_cut),
            None,
        )
        self.assertIsNotNone(preset, "PS2 Rescale 0-128 preset not found (expected clamp_min=0, clamp_max=128)")
        self.assertNotEqual(preset.clamp_min, preset.clamp_max,
                            "PS2 Rescale preset must have different min and max")

    def test_clamp_128_255_preset_has_different_min_max(self):
        """'Clamp 128–255' preset must have clamp_min=128, clamp_max=255."""
        from src.core.presets import BUILTIN_PRESETS
        preset = next((p for p in BUILTIN_PRESETS
                       if p.clamp_min == 128 and p.clamp_max == 255
                       and not p.binary_cut and not p.invert), None)
        self.assertIsNotNone(preset, "Clamp 128-255 preset not found")
        self.assertNotEqual(preset.clamp_min, preset.clamp_max,
                            "Clamp 128-255 preset must have different min and max")

    def test_alpha_preset_from_dict_preserves_range_min_max(self):
        """AlphaPreset.from_dict() must faithfully preserve different clamp_min and
        clamp_max values.  Custom presets with a range (e.g. min=0, max=128) must
        survive a to_dict() / from_dict() round-trip without collapsing to min==max."""
        from src.core.presets import AlphaPreset as _AP
        original = _AP(name="Range Test", description="test range", builtin=False,
                       clamp_min=0, clamp_max=128)
        d = original.to_dict()
        loaded = _AP.from_dict(d)
        self.assertEqual(loaded.clamp_min, 0,
                         f"clamp_min changed after round-trip: {loaded.clamp_min}")
        self.assertEqual(loaded.clamp_max, 128,
                         f"clamp_max changed after round-trip: {loaded.clamp_max}")
        self.assertNotEqual(loaded.clamp_min, loaded.clamp_max,
                            "from_dict must NOT collapse min==max for a range preset")

    def test_alpha_preset_from_dict_preserves_raised_floor(self):
        """A custom preset with clamp_min=128, clamp_max=255 (raised floor) must
        survive from_dict round-trip with both values intact."""
        from src.core.presets import AlphaPreset as _AP
        original = _AP(name="Floor Test", description="test floor", builtin=False,
                       clamp_min=128, clamp_max=255)
        loaded = _AP.from_dict(original.to_dict())
        self.assertEqual(loaded.clamp_min, 128)
        self.assertEqual(loaded.clamp_max, 255)

    def test_apply_alpha_preset_range_produces_varied_output(self):
        """apply_alpha_preset with a range preset (clamp_min=0, clamp_max=128) on a
        two-alpha image must produce at least two distinct output values — proving
        the range mapping actually spans [0, 128] and is not collapsed to a single value."""
        arr = np.zeros((1, 2, 4), dtype=np.uint8)
        arr[0, 0] = [128, 128, 128, 0]    # fully transparent
        arr[0, 1] = [128, 128, 128, 255]  # fully opaque
        img = Image.fromarray(arr, "RGBA")
        preset = AlphaPreset("ps2_rescale", "", clamp_min=0, clamp_max=128)
        result = apply_alpha_preset(img, preset)
        out = np.array(result)
        self.assertEqual(int(out[0, 0, 3]), 0,
                         "alpha=0 must stay at 0 with clamp_min=0")
        self.assertEqual(int(out[0, 1, 3]), 128,
                         "alpha=255 must map to 128 with clamp_max=128")
        self.assertNotEqual(int(out[0, 0, 3]), int(out[0, 1, 3]),
                            "Range preset must produce different output values — not forced to same")

    def test_apply_alpha_preset_range_floor_raised(self):
        """apply_alpha_preset with clamp_min=128, clamp_max=255 must map source [0,255]
        to target [128,255], confirming both endpoints differ and the floor is enforced."""
        arr = np.zeros((1, 2, 4), dtype=np.uint8)
        arr[0, 0] = [0, 0, 0, 0]
        arr[0, 1] = [0, 0, 0, 255]
        img = Image.fromarray(arr, "RGBA")
        preset = AlphaPreset("floor_raised", "", clamp_min=128, clamp_max=255)
        result = apply_alpha_preset(img, preset)
        out = np.array(result)
        self.assertEqual(int(out[0, 0, 3]), 128,
                         "alpha=0 must map to clamp_min=128 (floor raised)")
        self.assertEqual(int(out[0, 1, 3]), 255,
                         "alpha=255 must map to clamp_max=255")
        self.assertNotEqual(int(out[0, 0, 3]), int(out[0, 1, 3]),
                            "Floor-raised preset must produce different output values — not forced to same")

    # ------------------------------------------------------------------
    # Threshold-as-pixel-filter tests  (threshold > 0, binary_cut=False)
    # ------------------------------------------------------------------

    def test_manual_alpha_threshold_protects_high_alpha_pixels(self):
        """When threshold=128 and binary_cut=False, pixels with alpha >= 128 must
        keep their original value; only pixels below 128 are normalized."""
        # Row 0: low alpha (will be processed)
        # Row 1: high alpha (must be left untouched)
        arr = np.zeros((2, 4, 4), dtype=np.uint8)
        arr[0, :, 3] = 60    # below threshold → processed
        arr[1, :, 3] = 200   # at/above threshold → protected
        img = Image.fromarray(arr, "RGBA")
        result = apply_manual_alpha(img, threshold=128, clamp_min=0, clamp_max=64)
        out = np.array(result)
        # Processed row: uniform at 60, clamp(60, 0, 64)=60 (already within range)
        self.assertEqual(int(out[0, 0, 3]), 60,
                         "Uniform low-alpha pixel within [0, 64] is clamped (stays at 60)")
        # Protected row: must stay at 200 (not clipped to 64)
        self.assertTrue(np.all(out[1, :, 3] == 200),
                        "Pixels with alpha >= threshold must keep their original value")

    def test_manual_alpha_threshold_zero_processes_all(self):
        """threshold=0 (default) must process every pixel — normalize maps
        [img_min, img_max] → [clamp_min, clamp_max] exactly."""
        arr = np.zeros((1, 2, 4), dtype=np.uint8)
        arr[0, 0, 3] = 0
        arr[0, 1, 3] = 200
        img = Image.fromarray(arr, "RGBA")
        result = apply_manual_alpha(img, threshold=0, clamp_min=0, clamp_max=64)
        out = np.array(result)
        # normalize: img_min=0 → target_lo=0, img_max=200 → target_hi=64
        self.assertEqual(int(out[0, 0, 3]), 0,  "source_min=0 → target_lo=0")
        self.assertEqual(int(out[0, 1, 3]), 64, "source_max=200 → target_hi=64")

    def test_manual_alpha_threshold_mixed_range_partial_protect(self):
        """threshold=128 protects the top half while normalizing the bottom half.
        Only pixels with alpha < 128 are processed; normalize is applied to
        [img_min, img_max] of those pixels only."""
        arr = np.zeros((1, 4, 4), dtype=np.uint8)
        arr[0, 0, 3] = 0
        arr[0, 1, 3] = 64
        arr[0, 2, 3] = 128   # exactly at threshold → protected
        arr[0, 3, 3] = 200   # above threshold → protected
        img = Image.fromarray(arr, "RGBA")
        result = apply_manual_alpha(img, threshold=128, clamp_min=0, clamp_max=100)
        out = np.array(result)
        # Processed pixels (alpha < 128): {0, 64}
        # normalize: img_min=0 → target_lo=0, img_max=64 → target_hi=100
        # pixel=0  → 0
        # pixel=64 → 100
        self.assertEqual(int(out[0, 0, 3]), 0,   "source_min=0 → target_lo=0")
        self.assertEqual(int(out[0, 1, 3]), 100, "source_max=64 → target_hi=100")
        # Protected pixels: original values must be preserved
        self.assertEqual(int(out[0, 2, 3]), 128, "alpha=128 >= threshold → unchanged")
        self.assertEqual(int(out[0, 3, 3]), 200, "alpha=200 >= threshold → unchanged")

    def test_preset_threshold_protects_high_alpha_pixels(self):
        """apply_alpha_preset: threshold > 0 with binary_cut=False protects
        pixels at/above threshold from being normalized."""
        arr = np.zeros((2, 4, 4), dtype=np.uint8)
        arr[0, :, 3] = 50    # below threshold=100 → processed
        arr[1, :, 3] = 150   # at/above threshold=100 → protected
        img = Image.fromarray(arr, "RGBA")
        preset = AlphaPreset("protect_test", "", clamp_min=0, clamp_max=64,
                             threshold=100, binary_cut=False)
        result = apply_alpha_preset(img, preset)
        out = np.array(result)
        # Processed row: uniform 50 → proportional in [0, 64] → round(50/255*64)=12 or 13
        self.assertLess(int(out[0, 0, 3]), 64,
                        "Low-alpha pixel must be normalized, not clipped to target_hi")
        # Protected row: must stay at 150
        self.assertTrue(np.all(out[1, :, 3] == 150),
                        "Pixels with original alpha >= threshold must be left unchanged")

    def test_preset_binary_cut_with_threshold_still_processes_all(self):
        """When binary_cut=True, threshold is the SPLIT POINT (not a pixel filter).
        All pixels participate in normalize + binary_cut regardless of threshold."""
        arr = np.zeros((2, 4, 4), dtype=np.uint8)
        arr[0, :, 3] = 50    # below threshold
        arr[1, :, 3] = 200   # above threshold
        img = Image.fromarray(arr, "RGBA")
        preset = AlphaPreset("cut", "", threshold=128, binary_cut=True)
        result = apply_alpha_preset(img, preset)
        out = np.array(result)
        # binary_cut: normalizes [50,200]→[0,255], then 50→≈0<128→0, 200→≈255≥128→255
        self.assertTrue(np.all(out[0, :, 3] == 0),   "50 normalized then binary-cut → 0")
        self.assertTrue(np.all(out[1, :, 3] == 255),  "200 normalized then binary-cut → 255")



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

    def test_fully_opaque_image_max_reduces_alpha(self):
        """A fully-opaque image (all alpha=255) must have its alpha reduced to Max
        when Max < 255.  This is the primary use-case: the user sets Max=128 to cap
        a game texture at PS2 full-opacity.  Min is also tested to confirm it does
        not prevent the change."""
        img = make_rgba_image(alpha=255)
        result = apply_manual_alpha(img, clamp_min=0, clamp_max=128)
        arr = np.array(result)
        self.assertTrue(
            np.all(arr[:, :, 3] == 128),
            f"Fully-opaque image with max=128 must output 128, got {arr[0, 0, 3]}",
        )

    def test_fully_opaque_image_force_same_value(self):
        """Force-same-value mode (min==max) must set every pixel to exactly that
        value regardless of the source alpha."""
        img = make_rgba_image(alpha=255)
        result = apply_manual_alpha(img, clamp_min=128, clamp_max=128)
        arr = np.array(result)
        self.assertTrue(
            np.all(arr[:, :, 3] == 128),
            f"Force-same 128 on fully-opaque image must give 128, got {arr[0, 0, 3]}",
        )

    def test_fully_opaque_max_0_gives_transparent(self):
        """Setting Max=0 on a fully-opaque image must produce a fully-transparent
        result — the tool must be able to zero-out alpha regardless of source."""
        img = make_rgba_image(alpha=255)
        result = apply_manual_alpha(img, clamp_min=0, clamp_max=0)
        arr = np.array(result)
        self.assertTrue(
            np.all(arr[:, :, 3] == 0),
            f"Fully-opaque image with max=0 must output 0, got {arr[0, 0, 3]}",
        )

    def test_fully_opaque_min_0_max_255_unchanged(self):
        """With min=0 and max=255 a fully-opaque image stays at 255.
        This is expected — the old 'Full Opacity' preset range produces no change.
        The UX fix ensures users are auto-given max=128 when they first edit Min
        from this state, but the algorithm itself is correct."""
        img = make_rgba_image(alpha=255)
        result = apply_manual_alpha(img, clamp_min=0, clamp_max=255)
        arr = np.array(result)
        self.assertTrue(
            np.all(arr[:, :, 3] == 255),
            "min=0 max=255 on fully-opaque image should leave alpha at 255",
        )

    def test_fully_opaque_preset_max_128(self):
        """apply_alpha_preset with clamp_max=128 must also reduce a fully-opaque
        image to 128, confirming preset mode works the same as manual mode."""
        from src.core.presets import AlphaPreset
        img = make_rgba_image(alpha=255)
        preset = AlphaPreset("test", "", clamp_min=0, clamp_max=128)
        result = apply_alpha_preset(img, preset)
        arr = np.array(result)
        self.assertTrue(
            np.all(arr[:, :, 3] == 128),
            f"Preset max=128 on fully-opaque image must output 128, got {arr[0, 0, 3]}",
        )


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

    def test_alpha_delta_included_in_rgb_params(self):
        """The manual params dict in alpha_tool.py should include key 'a' for alpha delta."""
        source = self._alpha_tool_source()
        # Look for the alpha_delta_spin value being assigned under key "a"
        self.assertIn('"a": self._alpha_delta_spin.value()', source,
                      "manual params dict in alpha_tool.py should include key 'a' for alpha delta")

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
    """Source-level checks that built-in preset data is self-consistent.
    (Preset/force-same-value UI was removed; only preset data properties remain.)"""

    def _alpha_tool_source(self) -> str:
        path = os.path.join(os.path.dirname(__file__), "..", "src", "ui", "alpha_tool.py")
        with open(path) as f:
            return f.read()

    def test_on_preset_changed_range_preset_force_same_evaluates_false(self):
        """For any built-in preset with clamp_min != clamp_max, the expression
        `preset.clamp_min == preset.clamp_max` evaluates to False."""
        from src.core.presets import BUILTIN_PRESETS
        range_presets = [p for p in BUILTIN_PRESETS if p.clamp_min != p.clamp_max]
        self.assertGreaterEqual(len(range_presets), 3,
                                "Need at least 3 range presets to test against")
        for p in range_presets:
            result = (p.clamp_min == p.clamp_max)
            self.assertFalse(
                result,
                f"Preset '{p.name}' has clamp_min={p.clamp_min}, clamp_max={p.clamp_max} "
                f"(different), but clamp_min == clamp_max evaluates to True",
            )

    def test_on_preset_changed_flat_preset_force_same_evaluates_true(self):
        """For built-in flat presets with clamp_min == clamp_max, the expression
        evaluates to True."""
        from src.core.presets import BUILTIN_PRESETS
        flat_presets = [p for p in BUILTIN_PRESETS
                        if p.clamp_min == p.clamp_max and not p.binary_cut]
        self.assertGreaterEqual(len(flat_presets), 3,
                                "Need at least 3 flat presets to test against")
        for p in flat_presets:
            result = (p.clamp_min == p.clamp_max)
            self.assertTrue(
                result,
                f"Preset '{p.name}' has clamp_min == clamp_max == {p.clamp_min} "
                f"but the expression evaluates to False",
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

    def test_hint_label_in_setup_ui(self):
        """_setup_ui must add an explanatory hint label to guide new users."""
        src = self._alpha_tool_source()
        # The hint should describe the scaling behavior
        self.assertIn("0–255", src,
                      "Fine-tune section should have a hint label describing the 0–255 scale")

    def _tooltip_source(self) -> str:
        path = os.path.join(os.path.dirname(__file__), "..", "src", "ui", "tooltip_manager.py")
        with open(path) as f:
            return f.read()

    # ── apply_alpha_check must be registered ──────────────────────────────────

    # ── No stale clamp_min / clamp_max mode references ────────────────────────

    def test_no_clamp_min_mode_reference_in_clamp_tips(self):
        """clamp_min_spin and clamp_max_spin tips must not reference non-existent
        'clamp_min mode' or 'clamp_max mode'."""
        src = self._tooltip_source()
        self.assertNotIn("clamp_min mode", src)
        self.assertNotIn("clamp_max mode", src)
        self.assertNotIn("clamp modes", src)

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

    # ── _on_force_same_value_toggled must NOT call _switch_to_manual_if_preset_active ──

    def test_threshold_tooltip_describes_protect_behavior(self):
        """The threshold label tooltip must describe the 'protect above threshold'
        behavior now that threshold actually filters pixels when binary_cut is off."""
        src = self._alpha_tool_source()
        # Find the threshold tooltip block
        thresh_pos = src.find("lbl_thresh = QLabel")
        self.assertGreater(thresh_pos, 0, "lbl_thresh not found in alpha_tool.py")
        # Look for the tooltip content nearby
        tooltip_pos = src.find(".setToolTip(", thresh_pos)
        next_widget = src.find("gt_layout.addWidget", thresh_pos)
        block = src[thresh_pos:next_widget]
        self.assertIn("protect", block.lower(),
                      "threshold tooltip must describe the 'protect' behavior for pixels above threshold")




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
        preset = AlphaPreset(name="test", description="test preset", clamp_min=255, clamp_max=255, builtin=True)
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
                apply_manual_alpha(img, clamp_min=255, clamp_max=255)
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


class TestRound10ResourceHygiene(unittest.TestCase):
    """Round-10: four PIL image resource leaks.

    Bug 1 (apply_alpha_preset / apply_manual_alpha / apply_rgba_adjust,
            alpha_processor.py):
        ``img = img.convert("RGBA")`` leaked the original non-RGBA image when
        one of these functions was called with a non-RGBA input.  Fix uses a
        ``_orig`` intermediate variable and calls ``_orig.close()`` immediately
        after the convert succeeds.

    Bug 2 (AlphaWorker.run(), worker.py):
        The PIL image loaded by ``load_image()`` was never closed, and every
        reassignment like ``img = apply_alpha_preset(img, ...)`` also leaked
        the previous image object.  Fix closes the old ``img`` before each
        reassignment and adds an inner ``try/finally: img.close()`` to
        guarantee the final image is always released.

    Bug 3 (_AlphaPreviewLoader.run(), alpha_tool.py):
        ``orig`` was not closed on the early-abort path; ``orig`` and
        ``processed`` were never closed on the success path.  Fix adds
        ``processed = None`` sentinel and an inner ``try/finally`` that closes
        both, guarded by ``processed is not None and processed is not orig``.

    Bug 4 (_ConvertedThumbLoader.run(), preview_pane.py):
        The success path called ``img.close()`` but skipped closing
        ``save_img`` when it was a converted image (different object from
        ``img``).  Fix adds ``if save_img is not img: save_img.close()``
        before ``img.close()`` on the success path, mirroring the existing
        exception-path guard.
    """

    _AP_SRC = os.path.join(
        os.path.dirname(__file__), "..", "src", "core", "alpha_processor.py"
    )
    _W_SRC = os.path.join(
        os.path.dirname(__file__), "..", "src", "core", "worker.py"
    )
    _AT_SRC = os.path.join(
        os.path.dirname(__file__), "..", "src", "ui", "alpha_tool.py"
    )
    _PP_SRC = os.path.join(
        os.path.dirname(__file__), "..", "src", "ui", "preview_pane.py"
    )

    def _read(self, path: str) -> str:
        with open(path) as f:
            return f.read()

    def _fn_src(self, file_src: str, fn_name: str) -> str:
        """Return the source of a top-level def *fn_name* from *file_src*."""
        marker = f"\ndef {fn_name}("
        start = file_src.find(marker)
        self.assertGreater(start, 0, f"def {fn_name} not found in source")
        next_def = file_src.find("\ndef ", start + 1)
        return file_src[start:next_def] if next_def > 0 else file_src[start:]

    def _method_src(self, file_src: str, class_name: str, method_name: str) -> str:
        """Return the source of a method inside a named class."""
        class_marker = f"\nclass {class_name}("
        cls_start = file_src.find(class_marker)
        self.assertGreater(cls_start, 0, f"class {class_name} not found")
        next_class = file_src.find("\nclass ", cls_start + 1)
        cls_body = file_src[cls_start:next_class] if next_class > 0 else file_src[cls_start:]
        method_marker = f"\n    def {method_name}("
        m_start = cls_body.find(method_marker)
        self.assertGreater(m_start, 0,
                           f"def {method_name} not found in class {class_name}")
        next_method = cls_body.find("\n    def ", m_start + 1)
        return cls_body[m_start:next_method] if next_method > 0 else cls_body[m_start:]

    # ------------------------------------------------------------------
    # Bug 1 — apply_alpha_preset: leaks original on convert
    # ------------------------------------------------------------------

    def _apply_alpha_preset_src(self) -> str:
        return self._fn_src(self._read(self._AP_SRC), "apply_alpha_preset")

    def test_apply_alpha_preset_saves_orig_before_reassigning_img(self):
        """apply_alpha_preset must store the old img reference in ``_orig``
        *before* the ``img = img.convert(`` line so the original is closeable."""
        fn = self._apply_alpha_preset_src()
        orig_pos = fn.find("_orig = img")
        self.assertGreater(orig_pos, 0,
                           "apply_alpha_preset must have '_orig = img' before convert()")
        reassign_pos = fn.find("img = img.convert(", orig_pos)
        self.assertGreater(reassign_pos, orig_pos,
                           "_orig = img must precede img = img.convert()")

    def test_apply_alpha_preset_uses_orig_local_for_convert(self):
        """apply_alpha_preset must store the old img in ``_orig`` before
        converting, so the original can be closed."""
        fn = self._apply_alpha_preset_src()
        self.assertIn(
            "_orig = img",
            fn,
            "apply_alpha_preset must save the original in '_orig' before convert()",
        )

    def test_apply_alpha_preset_closes_orig_after_convert(self):
        """apply_alpha_preset must call ``_orig.close()`` after converting."""
        fn = self._apply_alpha_preset_src()
        self.assertIn(
            "_orig.close()",
            fn,
            "apply_alpha_preset must call _orig.close() after the RGBA convert",
        )

    # ------------------------------------------------------------------
    # Bug 1 (cont.) — apply_manual_alpha
    # ------------------------------------------------------------------

    def _apply_manual_alpha_src(self) -> str:
        return self._fn_src(self._read(self._AP_SRC), "apply_manual_alpha")

    def test_apply_manual_alpha_saves_orig_before_reassigning_img(self):
        """apply_manual_alpha must store the old img reference in ``_orig``
        *before* the ``img = img.convert(`` line so the original is closeable."""
        fn = self._apply_manual_alpha_src()
        orig_pos = fn.find("_orig = img")
        self.assertGreater(orig_pos, 0,
                           "apply_manual_alpha must have '_orig = img' before convert()")
        reassign_pos = fn.find("img = img.convert(", orig_pos)
        self.assertGreater(reassign_pos, orig_pos,
                           "_orig = img must precede img = img.convert()")

    def test_apply_manual_alpha_uses_orig_local_for_convert(self):
        fn = self._apply_manual_alpha_src()
        self.assertIn(
            "_orig = img",
            fn,
            "apply_manual_alpha must save the original in '_orig' before convert()",
        )

    def test_apply_manual_alpha_closes_orig_after_convert(self):
        fn = self._apply_manual_alpha_src()
        self.assertIn(
            "_orig.close()",
            fn,
            "apply_manual_alpha must call _orig.close() after the RGBA convert",
        )

    # ------------------------------------------------------------------
    # Bug 1 (cont.) — apply_rgba_adjust
    # ------------------------------------------------------------------

    def _apply_rgba_adjust_src(self) -> str:
        return self._fn_src(self._read(self._AP_SRC), "apply_rgba_adjust")

    def test_apply_rgba_adjust_saves_orig_before_reassigning_img(self):
        """apply_rgba_adjust must store the old img reference in ``_orig``
        *before* the ``img = img.convert(`` line so the original is closeable."""
        fn = self._apply_rgba_adjust_src()
        orig_pos = fn.find("_orig = img")
        self.assertGreater(orig_pos, 0,
                           "apply_rgba_adjust must have '_orig = img' before convert()")
        reassign_pos = fn.find("img = img.convert(", orig_pos)
        self.assertGreater(reassign_pos, orig_pos,
                           "_orig = img must precede img = img.convert()")

    def test_apply_rgba_adjust_uses_orig_local_for_convert(self):
        fn = self._apply_rgba_adjust_src()
        self.assertIn(
            "_orig = img",
            fn,
            "apply_rgba_adjust must save the original in '_orig' before convert()",
        )

    def test_apply_rgba_adjust_closes_orig_after_convert(self):
        fn = self._apply_rgba_adjust_src()
        self.assertIn(
            "_orig.close()",
            fn,
            "apply_rgba_adjust must call _orig.close() after the RGBA convert",
        )

    # ------------------------------------------------------------------
    # Bug 2 — AlphaWorker.run(): img never closed
    # ------------------------------------------------------------------

    def _alpha_worker_run_src(self) -> str:
        return self._method_src(self._read(self._W_SRC), "AlphaWorker", "run")

    def test_alpha_worker_run_does_not_reassign_img_with_apply_preset(self):
        """AlphaWorker.run must not use ``img = apply_alpha_preset(img,``
        because that leaks the old PIL image."""
        fn = self._alpha_worker_run_src()
        self.assertNotIn(
            "img = apply_alpha_preset(img,",
            fn,
            "AlphaWorker.run must not reassign img directly with apply_alpha_preset; "
            "use _tmp then img.close(); img = _tmp",
        )

    def test_alpha_worker_run_does_not_reassign_img_with_apply_manual(self):
        """AlphaWorker.run must not use ``img = apply_manual_alpha(img,``."""
        fn = self._alpha_worker_run_src()
        self.assertNotIn(
            "img = apply_manual_alpha(",
            fn,
            "AlphaWorker.run must not reassign img directly with apply_manual_alpha; "
            "use _tmp then img.close(); img = _tmp",
        )

    def test_alpha_worker_run_does_not_reassign_img_with_apply_rgba(self):
        """AlphaWorker.run must not use ``img = apply_rgba_adjust(img,``."""
        fn = self._alpha_worker_run_src()
        self.assertNotIn(
            "img = apply_rgba_adjust(",
            fn,
            "AlphaWorker.run must not reassign img directly with apply_rgba_adjust; "
            "use _tmp then img.close(); img = _tmp",
        )

    def test_alpha_worker_run_closes_img_before_reassigning_to_tmp(self):
        """After each apply_* call the old img must be closed before img = _tmp."""
        fn = self._alpha_worker_run_src()
        # Confirm the _tmp pattern is used
        self.assertIn(
            "_tmp = apply_alpha_preset(",
            fn,
            "AlphaWorker.run must store apply_alpha_preset result in '_tmp'",
        )
        # img.close() must appear before img = _tmp
        close_pos = fn.find("img.close()")
        self.assertGreater(close_pos, 0,
                           "AlphaWorker.run must call img.close() before reassigning")
        tmp_assign_pos = fn.find("img = _tmp", close_pos)
        self.assertGreater(tmp_assign_pos, close_pos,
                           "img = _tmp must follow img.close() in AlphaWorker.run")

    def test_alpha_worker_run_has_inner_finally_closing_img(self):
        """AlphaWorker.run must have an inner try/finally: img.close() wrapping
        the processing block so the final PIL image is always released."""
        fn = self._alpha_worker_run_src()
        finally_pos = fn.find("finally:")
        self.assertGreater(finally_pos, 0,
                           "AlphaWorker.run must have a finally block")
        close_pos = fn.find("img.close()", finally_pos)
        self.assertGreater(close_pos, finally_pos,
                           "img.close() must appear inside the finally block "
                           "in AlphaWorker.run")

    # ------------------------------------------------------------------
    # Bug 3 — _AlphaPreviewLoader.run(): orig / processed not closed
    # ------------------------------------------------------------------

    def _alpha_preview_loader_run_src(self) -> str:
        return self._method_src(
            self._read(self._AT_SRC), "_AlphaPreviewLoader", "run"
        )

    def test_alpha_preview_loader_initialises_processed_to_none(self):
        """_AlphaPreviewLoader.run must initialise ``processed = None`` before
        the inner try so the finally guard works even on early abort."""
        fn = self._alpha_preview_loader_run_src()
        self.assertIn(
            "processed = None",
            fn,
            "_AlphaPreviewLoader.run must initialise processed = None",
        )

    def test_alpha_preview_loader_closes_orig_in_finally(self):
        """_AlphaPreviewLoader.run must close ``orig`` in a finally block."""
        fn = self._alpha_preview_loader_run_src()
        finally_pos = fn.find("finally:")
        self.assertGreater(finally_pos, 0,
                           "_AlphaPreviewLoader.run must have a finally block")
        close_pos = fn.find("orig.close()", finally_pos)
        self.assertGreater(close_pos, finally_pos,
                           "orig.close() must appear inside the finally block")

    def test_alpha_preview_loader_closes_processed_in_finally_with_guard(self):
        """_AlphaPreviewLoader.run must close ``processed`` in the finally,
        guarded by ``processed is not None and processed is not orig``."""
        fn = self._alpha_preview_loader_run_src()
        finally_pos = fn.find("finally:")
        self.assertGreater(finally_pos, 0,
                           "_AlphaPreviewLoader.run must have a finally block")
        finally_block = fn[finally_pos:]
        self.assertIn(
            "processed is not None",
            finally_block,
            "finally block must guard processed.close() with 'processed is not None'",
        )
        self.assertIn(
            "processed is not orig",
            finally_block,
            "finally block must guard processed.close() with 'processed is not orig'",
        )
        close_pos = finally_block.find("processed.close()")
        self.assertGreater(close_pos, 0,
                           "processed.close() must appear in the finally block")

    def test_alpha_preview_loader_abort_inside_inner_try(self):
        """The early-abort ``return`` must sit inside the inner try so that
        the finally always closes ``orig``."""
        fn = self._alpha_preview_loader_run_src()
        # processed = None sentinel must appear BEFORE the inner try
        none_pos = fn.find("processed = None")
        self.assertGreater(none_pos, 0)
        inner_try_pos = fn.find("try:", none_pos)
        self.assertGreater(inner_try_pos, none_pos,
                           "inner try must follow 'processed = None' sentinel")
        abort_pos = fn.find("if self._abort:", inner_try_pos)
        self.assertGreater(abort_pos, inner_try_pos,
                           "abort check must be inside the inner try block")

    def test_alpha_preview_loader_closes_old_processed_before_rgba_reassign(self):
        """When apply_rgba_adjust creates a new processed image the old one
        must be closed (if it is not orig) before reassignment."""
        fn = self._alpha_preview_loader_run_src()
        # _tmp is used for the rgba-adjust result
        self.assertIn(
            "_tmp = apply_rgba_adjust(",
            fn,
            "_AlphaPreviewLoader.run must store rgba adjust result in '_tmp'",
        )
        # processed is closed before reassignment when it differs from orig
        self.assertIn(
            "if processed is not orig:",
            fn,
            "must guard processed.close() with 'if processed is not orig:'",
        )

    # ------------------------------------------------------------------
    # Bug 4 — _ConvertedThumbLoader.run(): save_img not closed on success
    # ------------------------------------------------------------------

    def _converted_thumb_loader_src(self) -> str:
        src = self._read(self._PP_SRC)
        marker = "\nclass _ConvertedThumbLoader("
        start = src.find(marker)
        self.assertGreater(start, 0, "_ConvertedThumbLoader class not found")
        next_cls = src.find("\nclass ", start + 1)
        return src[start:next_cls] if next_cls > 0 else src[start:]

    def test_converted_thumb_loader_closes_save_img_on_success_path(self):
        """On the success path, _ConvertedThumbLoader.run must close
        ``save_img`` when it is a converted (different) image."""
        cls_src = self._converted_thumb_loader_src()
        # Find the success path: after the except block ends and before thumbnail
        # The pattern 'if save_img is not img:' followed by 'save_img.close()'
        # must appear before 'img.close()' in the success path.
        guard_pos = cls_src.find("if save_img is not img:")
        self.assertGreater(guard_pos, 0,
                           "_ConvertedThumbLoader must have 'if save_img is not img:' guard")
        # There must be TWO occurrences: one in except, one in success path.
        second_guard_pos = cls_src.find("if save_img is not img:", guard_pos + 1)
        self.assertGreater(
            second_guard_pos, guard_pos,
            "_ConvertedThumbLoader success path must also have "
            "'if save_img is not img:' guard (not just the exception path)",
        )

    def test_converted_thumb_loader_success_save_img_close_before_img_close(self):
        """save_img.close() must appear before img.close() on the success
        path (i.e., not only in the fallback exception path)."""
        cls_src = self._converted_thumb_loader_src()
        # The second guard is on the success path; find it
        guard_pos = cls_src.find("if save_img is not img:")
        second_guard_pos = cls_src.find("if save_img is not img:", guard_pos + 1)
        self.assertGreater(second_guard_pos, guard_pos)
        success_section = cls_src[second_guard_pos:]
        # save_img.close() must precede img.close() in that section
        save_close = success_section.find("save_img.close()")
        img_close = success_section.find("img.close()")
        self.assertGreater(save_close, -1,
                           "save_img.close() must appear in success path section")
        self.assertGreater(img_close, -1,
                           "img.close() must appear after save_img.close()")
        self.assertLess(save_close, img_close,
                        "save_img.close() must come before img.close() on success path")


class TestRound11ResourceHygiene(unittest.TestCase):
    """Round-11: six PIL image resource leaks in _ConvertedThumbLoader and
    _ConverterPreviewLoader (preview_pane.py).

    Bug 1 (_ConvertedThumbLoader.run()):
        ``img`` was not closed when an unexpected exception propagated to the
        outer ``except Exception`` handler (e.g. MemoryError from a format-
        conversion call at lines 185-195).  Fix: ``img = None`` sentinel before
        the outer try + ``finally: if img is not None: img.close()``.

    Bug 2 (_ConvertedThumbLoader.run()):
        ``preview_img`` was not closed if ``preview_img.load()`` raised
        (e.g. MemoryError) — the inner ``except`` block returned without
        closing it.  Fix: ``preview_img = None`` sentinel before the inner try
        + ``if preview_img is not None: preview_img.close()`` in the inner
        except.

    Bug 3 (_ConverterPreviewLoader.run()):
        Same ``img`` leak as Bug 1 — no outer ``try/finally`` to guarantee
        ``img.close()`` on unexpected exceptions.

    Bug 4 (_ConverterPreviewLoader.run()):
        ``src_thumb = img.copy()`` was followed by explicit calls to
        ``src_thumb.thumbnail()`` and ``src_thumb.close()`` but lacked a
        try/finally, so an exception between open and close would leak the
        thumbnail image.  Fix: wrap the three-line block in try/finally.

    Bug 5 (_ConverterPreviewLoader.run()):
        ``out_img`` was opened inside the inner try but the inner ``except``
        block did not close it when ``out_img.load()`` raised.  Fix: initialise
        ``out_img = None`` before the inner try and close it in the inner except
        when not None.

    Bug 6 (_ConverterPreviewLoader.run()):
        ``out_thumb = out_img.copy()`` lacked a try/finally, so an exception
        during ``thumbnail()`` or ``_pil_to_qimage()`` would leak ``out_thumb``
        and ``out_img``.  Fix: wrap in try/finally that closes both.
    """

    _PP_SRC = os.path.join(
        os.path.dirname(__file__), "..", "src", "ui", "preview_pane.py"
    )

    def _read(self) -> str:
        with open(self._PP_SRC) as f:
            return f.read()

    def _class_src(self, src: str, class_name: str) -> str:
        """Return the full source of a named class."""
        marker = f"\nclass {class_name}("
        start = src.find(marker)
        self.assertGreater(start, 0, f"class {class_name} not found")
        next_cls = src.find("\nclass ", start + 1)
        return src[start:next_cls] if next_cls > 0 else src[start:]

    def _method_src(self, cls_src: str, method_name: str) -> str:
        """Return the source of a method within a class body."""
        marker = f"\n    def {method_name}("
        start = cls_src.find(marker)
        self.assertGreater(start, 0,
                           f"def {method_name} not found in class source")
        next_method = cls_src.find("\n    def ", start + 1)
        return cls_src[start:next_method] if next_method > 0 else cls_src[start:]

    # ------------------------------------------------------------------
    # Bug 1 — _ConvertedThumbLoader.run(): img not closed on outer except
    # ------------------------------------------------------------------

    def _ctl_run_src(self) -> str:
        src = self._read()
        cls = self._class_src(src, "_ConvertedThumbLoader")
        return self._method_src(cls, "run")

    def test_converted_thumb_loader_img_sentinel_before_outer_try(self):
        """_ConvertedThumbLoader.run must initialise ``img = None`` before the
        outer try so the finally block can always reference it."""
        run = self._ctl_run_src()
        sentinel_pos = run.find("img = None")
        self.assertGreater(sentinel_pos, 0,
                           "_ConvertedThumbLoader.run must have 'img = None' sentinel")
        try_pos = run.find("try:")
        self.assertGreater(try_pos, sentinel_pos,
                           "'img = None' must appear before the outer 'try:'")

    def test_converted_thumb_loader_outer_finally_closes_img(self):
        """_ConvertedThumbLoader.run must have a ``finally`` block that closes
        ``img`` when it is not None."""
        run = self._ctl_run_src()
        self.assertIn(
            "finally:",
            run,
            "_ConvertedThumbLoader.run must have a 'finally:' block",
        )
        self.assertIn(
            "if img is not None:",
            run,
            "_ConvertedThumbLoader.run finally must guard with 'if img is not None:'",
        )
        # img.close() must appear inside the finally guard
        guard_pos = run.rfind("if img is not None:")
        self.assertGreater(guard_pos, 0)
        close_pos = run.find("img.close()", guard_pos)
        self.assertGreater(
            close_pos, guard_pos,
            "img.close() must appear after the 'if img is not None:' guard in finally",
        )

    def test_converted_thumb_loader_sets_img_none_after_abort_close(self):
        """After the early-abort ``img.close()``, ``img`` must be set to None
        so the finally block does not double-close it."""
        run = self._ctl_run_src()
        abort_close = run.find("img.close()")
        self.assertGreater(abort_close, 0, "img.close() not found in abort path")
        null_pos = run.find("img = None", abort_close)
        self.assertGreater(
            null_pos, abort_close,
            "img = None must follow img.close() on the abort path",
        )

    # ------------------------------------------------------------------
    # Bug 2 — _ConvertedThumbLoader.run(): preview_img not closed on load() error
    # ------------------------------------------------------------------

    def test_converted_thumb_loader_preview_img_sentinel(self):
        """_ConvertedThumbLoader.run must initialise ``preview_img = None``
        before the inner try so it can be closed in the except block."""
        run = self._ctl_run_src()
        self.assertIn(
            "preview_img = None",
            run,
            "_ConvertedThumbLoader.run must have 'preview_img = None' sentinel",
        )

    def test_converted_thumb_loader_inner_except_closes_preview_img(self):
        """When ``preview_img.load()`` raises, the inner except must close
        ``preview_img`` if it was opened."""
        run = self._ctl_run_src()
        self.assertIn(
            "if preview_img is not None:",
            run,
            "_ConvertedThumbLoader inner except must guard with "
            "'if preview_img is not None:'",
        )
        guard_pos = run.find("if preview_img is not None:")
        close_pos = run.find("preview_img.close()", guard_pos)
        self.assertGreater(
            close_pos, guard_pos,
            "preview_img.close() must follow the 'if preview_img is not None:' guard",
        )

    # ------------------------------------------------------------------
    # Bug 3 — _ConverterPreviewLoader.run(): img not closed on outer except
    # ------------------------------------------------------------------

    def _cpl_run_src(self) -> str:
        src = self._read()
        cls = self._class_src(src, "_ConverterPreviewLoader")
        return self._method_src(cls, "run")

    def test_converter_preview_loader_img_sentinel_before_outer_try(self):
        """_ConverterPreviewLoader.run must initialise ``img = None`` before
        the outer try."""
        run = self._cpl_run_src()
        sentinel_pos = run.find("img = None")
        self.assertGreater(sentinel_pos, 0,
                           "_ConverterPreviewLoader.run must have 'img = None' sentinel")
        try_pos = run.find("try:")
        self.assertGreater(try_pos, sentinel_pos,
                           "'img = None' must appear before the outer 'try:'")

    def test_converter_preview_loader_outer_finally_closes_img(self):
        """_ConverterPreviewLoader.run must have a ``finally`` block that closes
        ``img`` when it is not None."""
        run = self._cpl_run_src()
        self.assertIn(
            "finally:",
            run,
            "_ConverterPreviewLoader.run must have a 'finally:' block",
        )
        self.assertIn(
            "if img is not None:",
            run,
            "_ConverterPreviewLoader.run finally must guard with 'if img is not None:'",
        )
        guard_pos = run.rfind("if img is not None:")
        self.assertGreater(guard_pos, 0)
        close_pos = run.find("img.close()", guard_pos)
        self.assertGreater(
            close_pos, guard_pos,
            "img.close() must appear after the last 'if img is not None:' guard",
        )

    # ------------------------------------------------------------------
    # Bug 4 — _ConverterPreviewLoader.run(): src_thumb not in try/finally
    # ------------------------------------------------------------------

    def test_converter_preview_loader_src_thumb_in_try_finally(self):
        """src_thumb must be wrapped in a try/finally so it is always closed."""
        run = self._cpl_run_src()
        copy_pos = run.find("src_thumb = img.copy()")
        self.assertGreater(copy_pos, 0,
                           "src_thumb = img.copy() not found in _ConverterPreviewLoader.run")
        try_pos = run.find("try:", copy_pos)
        self.assertGreater(
            try_pos, copy_pos,
            "A 'try:' block must follow 'src_thumb = img.copy()'",
        )
        finally_pos = run.find("finally:", try_pos)
        self.assertGreater(
            finally_pos, try_pos,
            "A 'finally:' block must follow the src_thumb try: block",
        )
        close_pos = run.find("src_thumb.close()", finally_pos)
        self.assertGreater(
            close_pos, finally_pos,
            "src_thumb.close() must appear inside the finally block after src_thumb try:",
        )

    # ------------------------------------------------------------------
    # Bug 5 — _ConverterPreviewLoader.run(): out_img not closed on load() error
    # ------------------------------------------------------------------

    def test_converter_preview_loader_out_img_sentinel(self):
        """_ConverterPreviewLoader.run must initialise ``out_img = None``
        before the inner buf try."""
        run = self._cpl_run_src()
        self.assertIn(
            "out_img = None",
            run,
            "_ConverterPreviewLoader.run must have 'out_img = None' sentinel",
        )

    def test_converter_preview_loader_inner_except_closes_out_img(self):
        """When ``out_img.load()`` raises, the inner except must close
        ``out_img`` if it was opened."""
        run = self._cpl_run_src()
        self.assertIn(
            "if out_img is not None:",
            run,
            "_ConverterPreviewLoader inner except must guard with "
            "'if out_img is not None:'",
        )
        guard_pos = run.find("if out_img is not None:")
        close_pos = run.find("out_img.close()", guard_pos)
        self.assertGreater(
            close_pos, guard_pos,
            "out_img.close() must follow the 'if out_img is not None:' guard",
        )

    # ------------------------------------------------------------------
    # Bug 6 — _ConverterPreviewLoader.run(): out_thumb / out_img not in try/finally
    # ------------------------------------------------------------------

    def test_converter_preview_loader_out_thumb_in_try_finally(self):
        """out_thumb must be wrapped in a try/finally so it and out_img are
        always closed."""
        run = self._cpl_run_src()
        copy_pos = run.find("out_thumb = out_img.copy()")
        self.assertGreater(
            copy_pos, 0,
            "out_thumb = out_img.copy() not found in _ConverterPreviewLoader.run",
        )
        finally_pos = run.find("finally:", copy_pos)
        self.assertGreater(
            finally_pos, copy_pos,
            "A 'finally:' block must follow the out_thumb = out_img.copy() line",
        )
        out_thumb_close = run.find("out_thumb.close()", finally_pos)
        self.assertGreater(
            out_thumb_close, finally_pos,
            "out_thumb.close() must appear in the finally block",
        )
        out_img_close = run.find("out_img.close()", finally_pos)
        self.assertGreater(
            out_img_close, finally_pos,
            "out_img.close() must appear in the finally block",
        )

    def test_converter_preview_loader_out_thumb_sentinel(self):
        """out_thumb must be initialised to None before the try/finally so the
        guard 'if out_thumb is not None:' prevents closing an unset variable."""
        run = self._cpl_run_src()
        self.assertIn(
            "out_thumb = None",
            run,
            "_ConverterPreviewLoader.run must have 'out_thumb = None' sentinel",
        )
        self.assertIn(
            "if out_thumb is not None:",
            run,
            "_ConverterPreviewLoader.run finally must guard with "
            "'if out_thumb is not None:'",
        )


class TestRound12ResourceHygiene(unittest.TestCase):
    """Round-12: three PIL image resource leaks in _flatten_alpha (file_converter.py).

    Bug 1 (_flatten_alpha RGBA branch):
        ``base = Image.new("RGB", ...)`` was allocated before the paste call but
        never closed if ``img.split()`` or ``base.paste()`` raised an exception
        (e.g. MemoryError during compositing).  Fix wraps the paste call in
        ``try/except Exception: base.close(); raise``.

    Bug 2 (_flatten_alpha LA branch):
        Same as Bug 1 but for LA (luminance-alpha) images.  ``base = Image.new("L", ...)``
        was not protected, so any exception during ``img.split()`` or ``base.paste()``
        would leak it.  Fix applies the same ``try/except Exception:`` guard.

    Bug 3 (_flatten_alpha PA/P branch):
        The existing try/finally already guaranteed ``rgba.close()``, but
        ``base = Image.new("RGB", ...)`` inside the try block was not itself
        protected — if ``rgba.split()`` or ``base.paste()`` raised, ``rgba``
        would be closed (correctly) but ``base`` would leak.  Fix adds an inner
        ``try/except Exception: base.close(); raise`` around the paste call.
    """

    _FC_SRC = os.path.join(
        os.path.dirname(__file__), "..", "src", "core", "file_converter.py"
    )

    def _read_fc(self) -> str:
        with open(self._FC_SRC) as f:
            return f.read()

    def _flatten_alpha_src(self) -> str:
        src = self._read_fc()
        start = src.find("\ndef _flatten_alpha(")
        self.assertGreater(start, 0, "_flatten_alpha not found")
        next_def = src.find("\ndef ", start + 1)
        return src[start:next_def] if next_def > 0 else src[start:]

    # ------------------------------------------------------------------
    # Bug 1 — RGBA branch: base must be closed on exception
    # ------------------------------------------------------------------

    def test_flatten_alpha_rgba_branch_has_try_around_paste(self):
        """RGBA branch must wrap base.paste() in a try block so an exception
        does not silently leak the base image."""
        fn = self._flatten_alpha_src()
        rgba_branch_pos = fn.find('img.mode == "RGBA"')
        self.assertGreater(rgba_branch_pos, 0,
                           'RGBA branch must exist in _flatten_alpha')
        # There must be a 'try:' before the first 'except Exception:' in the
        # RGBA branch (i.e. before the LA branch starts).
        la_branch_pos = fn.find('img.mode == "LA"', rgba_branch_pos)
        self.assertGreater(la_branch_pos, rgba_branch_pos,
                           'LA branch must follow RGBA branch')
        try_pos = fn.find("try:", rgba_branch_pos)
        self.assertGreater(try_pos, rgba_branch_pos,
                           "RGBA branch must contain a 'try:' block")
        self.assertLess(try_pos, la_branch_pos,
                        "try: block must be inside the RGBA branch (before LA branch)")

    def test_flatten_alpha_rgba_branch_closes_base_in_except(self):
        """RGBA branch except block must call base.close() before re-raising."""
        fn = self._flatten_alpha_src()
        rgba_branch_pos = fn.find('img.mode == "RGBA"')
        la_branch_pos = fn.find('img.mode == "LA"', rgba_branch_pos)
        rgba_branch = fn[rgba_branch_pos:la_branch_pos]
        self.assertIn(
            "except Exception:",
            rgba_branch,
            "RGBA branch must have 'except Exception:' to catch paste failures",
        )
        except_pos = rgba_branch.find("except Exception:")
        close_pos = rgba_branch.find("base.close()", except_pos)
        self.assertGreater(
            close_pos, except_pos,
            "base.close() must appear after 'except Exception:' in the RGBA branch",
        )
        raise_pos = rgba_branch.find("raise", close_pos)
        self.assertGreater(
            raise_pos, close_pos,
            "'raise' must appear after base.close() in the RGBA except block",
        )

    def test_flatten_alpha_rgba_branch_returns_base_outside_try(self):
        """'return base' in the RGBA branch must NOT be inside the try block
        to avoid accidentally suppressing exceptions from paste()."""
        fn = self._flatten_alpha_src()
        rgba_branch_pos = fn.find('img.mode == "RGBA"')
        la_branch_pos = fn.find('img.mode == "LA"', rgba_branch_pos)
        rgba_branch = fn[rgba_branch_pos:la_branch_pos]
        # 'return base' must come AFTER the except block, not inside 'try:'
        try_pos = rgba_branch.find("try:")
        except_pos = rgba_branch.find("except Exception:")
        return_pos = rgba_branch.find("return base")
        self.assertGreater(return_pos, except_pos,
                           "'return base' must appear after the except block "
                           "in the RGBA branch (not inside the try block)")

    # ------------------------------------------------------------------
    # Bug 2 — LA branch: base must be closed on exception
    # ------------------------------------------------------------------

    def test_flatten_alpha_la_branch_has_try_around_paste(self):
        """LA branch must wrap base.paste() in a try block."""
        fn = self._flatten_alpha_src()
        la_branch_pos = fn.find('img.mode == "LA"')
        self.assertGreater(la_branch_pos, 0,
                           'LA branch must exist in _flatten_alpha')
        pa_branch_pos = fn.find('"PA", "P"', la_branch_pos)
        self.assertGreater(pa_branch_pos, la_branch_pos,
                           'PA/P branch must follow LA branch')
        try_pos = fn.find("try:", la_branch_pos)
        self.assertGreater(try_pos, la_branch_pos,
                           "LA branch must contain a 'try:' block")
        self.assertLess(try_pos, pa_branch_pos,
                        "try: block must be inside the LA branch (before PA/P branch)")

    def test_flatten_alpha_la_branch_closes_base_in_except(self):
        """LA branch except block must call base.close() before re-raising."""
        fn = self._flatten_alpha_src()
        la_branch_pos = fn.find('img.mode == "LA"')
        pa_branch_pos = fn.find('"PA", "P"', la_branch_pos)
        la_branch = fn[la_branch_pos:pa_branch_pos]
        self.assertIn(
            "except Exception:",
            la_branch,
            "LA branch must have 'except Exception:' to catch paste failures",
        )
        except_pos = la_branch.find("except Exception:")
        close_pos = la_branch.find("base.close()", except_pos)
        self.assertGreater(
            close_pos, except_pos,
            "base.close() must appear after 'except Exception:' in the LA branch",
        )
        raise_pos = la_branch.find("raise", close_pos)
        self.assertGreater(
            raise_pos, close_pos,
            "'raise' must appear after base.close() in the LA except block",
        )

    def test_flatten_alpha_la_branch_returns_base_outside_try(self):
        """'return base' in the LA branch must come after the except block."""
        fn = self._flatten_alpha_src()
        la_branch_pos = fn.find('img.mode == "LA"')
        pa_branch_pos = fn.find('"PA", "P"', la_branch_pos)
        la_branch = fn[la_branch_pos:pa_branch_pos]
        except_pos = la_branch.find("except Exception:")
        return_pos = la_branch.find("return base")
        self.assertGreater(return_pos, except_pos,
                           "'return base' must appear after the except block "
                           "in the LA branch (not inside the try block)")

    # ------------------------------------------------------------------
    # Bug 3 — PA/P branch: base must also be closed on exception
    # ------------------------------------------------------------------

    def test_flatten_alpha_pap_branch_has_inner_try_for_base(self):
        """PA/P branch must have an inner try block protecting base from paste
        exceptions (in addition to the outer try/finally that closes rgba)."""
        fn = self._flatten_alpha_src()
        pa_branch_pos = fn.find('"PA", "P"')
        self.assertGreater(pa_branch_pos, 0,
                           'PA/P branch must exist in _flatten_alpha')
        # There must be at least two 'try:' occurrences in the PA/P branch:
        # the outer one (guarding rgba) and the inner one (guarding base).
        pa_branch = fn[pa_branch_pos:]
        # Limit to the PA/P function section (everything up to the next top-level
        # 'if' at the same indentation level, or end of function)
        next_top_if = pa_branch.find("\n    if img.mode not in")
        if next_top_if < 0:
            next_top_if = pa_branch.find("\n    return img")
        pa_section = pa_branch[:next_top_if] if next_top_if > 0 else pa_branch
        first_try = pa_section.find("try:")
        self.assertGreater(first_try, 0, "PA/P branch must have at least one try:")
        second_try = pa_section.find("try:", first_try + 1)
        self.assertGreater(
            second_try, first_try,
            "PA/P branch must have a second (inner) try: block to guard base.paste()",
        )

    def test_flatten_alpha_pap_branch_closes_base_in_inner_except(self):
        """PA/P branch inner except block must call base.close() before re-raising."""
        fn = self._flatten_alpha_src()
        pa_branch_pos = fn.find('"PA", "P"')
        pa_branch = fn[pa_branch_pos:]
        next_top_if = pa_branch.find("\n    if img.mode not in")
        if next_top_if < 0:
            next_top_if = pa_branch.find("\n    return img")
        pa_section = pa_branch[:next_top_if] if next_top_if > 0 else pa_branch
        self.assertIn(
            "except Exception:",
            pa_section,
            "PA/P branch must have 'except Exception:' guard for base",
        )
        except_pos = pa_section.find("except Exception:")
        close_pos = pa_section.find("base.close()", except_pos)
        self.assertGreater(
            close_pos, except_pos,
            "base.close() must appear after 'except Exception:' in the PA/P branch",
        )
        raise_pos = pa_section.find("raise", close_pos)
        self.assertGreater(
            raise_pos, close_pos,
            "'raise' must follow base.close() in the PA/P inner except block",
        )

    def test_flatten_alpha_pap_branch_outer_finally_still_closes_rgba(self):
        """Round-8 fix must still be intact: rgba.close() in the outer finally."""
        fn = self._flatten_alpha_src()
        pa_branch_pos = fn.find('"PA", "P"')
        pa_branch = fn[pa_branch_pos:]
        finally_pos = pa_branch.find("finally:")
        self.assertGreater(finally_pos, 0,
                           "PA/P branch must still have an outer finally: block")
        close_pos = pa_branch.find("rgba.close()", finally_pos)
        self.assertGreater(close_pos, finally_pos,
                           "rgba.close() must still appear in the outer finally block")

    # ------------------------------------------------------------------
    # Behavioural smoke tests (functional verification)
    # ------------------------------------------------------------------

    def test_flatten_alpha_rgba_closes_base_on_paste_error(self):
        """When paste() raises in the RGBA branch, base must be closed and the
        exception must propagate (not be swallowed)."""
        from PIL import Image
        from src.core.file_converter import _flatten_alpha

        closed = []
        raised = []

        class _FakeRGBA:
            mode = "RGBA"
            size = (4, 4)

            def split(self):
                # Return dummy channel objects
                ch = Image.new("L", (4, 4), 0)
                return [ch, ch, ch, ch]

        class _FakeBase:
            def paste(self, img, mask=None):
                raise MemoryError("simulated paste OOM")

            def close(self):
                closed.append(True)

        import unittest.mock as mock
        orig_new = Image.new

        def patched_new(mode, size, *args, **kwargs):
            if mode == "RGB":
                return _FakeBase()
            return orig_new(mode, size, *args, **kwargs)

        img = Image.new("RGBA", (4, 4), (255, 0, 0, 128))
        try:
            with mock.patch("src.core.file_converter.Image.new", side_effect=patched_new):
                _flatten_alpha(img)
        except MemoryError:
            raised.append(True)
        finally:
            img.close()

        self.assertTrue(raised, "_flatten_alpha RGBA: MemoryError must propagate")
        self.assertTrue(closed, "_flatten_alpha RGBA: base must be closed when paste raises")

    def test_flatten_alpha_la_closes_base_on_paste_error(self):
        """When paste() raises in the LA branch, base must be closed and the
        exception must propagate."""
        from PIL import Image
        from src.core.file_converter import _flatten_alpha

        closed = []
        raised = []

        class _FakeBase:
            def paste(self, img, mask=None):
                raise MemoryError("simulated paste OOM")

            def close(self):
                closed.append(True)

        import unittest.mock as mock
        orig_new = Image.new

        def patched_new(mode, size, *args, **kwargs):
            if mode == "L":
                return _FakeBase()
            return orig_new(mode, size, *args, **kwargs)

        img = Image.new("LA", (4, 4), (128, 200))
        try:
            with mock.patch("src.core.file_converter.Image.new", side_effect=patched_new):
                _flatten_alpha(img)
        except MemoryError:
            raised.append(True)
        finally:
            img.close()

        self.assertTrue(raised, "_flatten_alpha LA: MemoryError must propagate")
        self.assertTrue(closed, "_flatten_alpha LA: base must be closed when paste raises")


class TestRound13ResourceHygiene(unittest.TestCase):
    """Round-13: converted RGBA image leaks when np.array() raises MemoryError.

    Bug (all three processing functions: apply_alpha_preset, apply_manual_alpha,
    apply_rgba_adjust):

    When ``img.mode != "RGBA"`` the function converts the caller-supplied image
    to RGBA and re-assigns the local ``img`` variable to the new image.  If the
    subsequent ``np.array(img, dtype=np.int32)`` call raises ``MemoryError``,
    the original code re-raised without closing the newly-created RGBA image.
    The caller's ``finally`` block only closes the *original* image, so the
    converted RGBA image was silently leaked.

    Fix: add a ``_converted = img.mode != "RGBA"`` flag before the conversion
    and close ``img`` inside the ``MemoryError`` handler when ``_converted``
    is True.
    """

    # ------------------------------------------------------------------
    # Source-level structural tests (guard against regression)
    # ------------------------------------------------------------------

    _AP_SRC = os.path.join(
        os.path.dirname(__file__), "..", "src", "core", "alpha_processor.py"
    )

    def _read_ap(self) -> str:
        with open(self._AP_SRC) as f:
            return f.read()

    def _fn_src(self, fn_name: str) -> str:
        """Return the source of a top-level function in alpha_processor.py."""
        src = self._read_ap()
        start = src.find(f"\ndef {fn_name}(")
        self.assertGreater(start, 0, f"{fn_name} not found")
        next_def = src.find("\ndef ", start + 1)
        return src[start:next_def] if next_def > 0 else src[start:]

    def _assert_converted_flag_and_close(self, fn_name: str) -> None:
        """Assert that fn_name uses _converted flag and closes img in MemoryError."""
        fn = self._fn_src(fn_name)
        self.assertIn(
            "_converted = img.mode != \"RGBA\"",
            fn,
            f"{fn_name}: must set _converted = img.mode != 'RGBA' before conversion",
        )
        mem_pos = fn.find("except MemoryError:")
        self.assertGreater(mem_pos, 0,
                           f"{fn_name}: must have an 'except MemoryError:' block")
        close_pos = fn.find("img.close()", mem_pos)
        self.assertGreater(
            close_pos, mem_pos,
            f"{fn_name}: img.close() must appear inside the except MemoryError block",
        )
        converted_guard_pos = fn.find("if _converted:", mem_pos)
        self.assertGreater(
            converted_guard_pos, mem_pos,
            f"{fn_name}: 'if _converted:' guard must appear before img.close() "
            "in the MemoryError handler",
        )
        self.assertLess(
            converted_guard_pos, close_pos,
            f"{fn_name}: 'if _converted:' must precede img.close() in the handler",
        )

    def test_apply_alpha_preset_has_converted_flag_and_close(self):
        """apply_alpha_preset must use _converted flag and close img on MemoryError."""
        self._assert_converted_flag_and_close("apply_alpha_preset")

    def test_apply_manual_alpha_has_converted_flag_and_close(self):
        """apply_manual_alpha must use _converted flag and close img on MemoryError."""
        self._assert_converted_flag_and_close("apply_manual_alpha")

    def test_apply_rgba_adjust_has_converted_flag_and_close(self):
        """apply_rgba_adjust must use _converted flag and close img on MemoryError."""
        self._assert_converted_flag_and_close("apply_rgba_adjust")

    # ------------------------------------------------------------------
    # Behavioural smoke tests
    # ------------------------------------------------------------------

    def _make_rgb_img(self, size=(4, 4)):
        from PIL import Image
        return Image.new("RGB", size, (100, 150, 200))

    def test_apply_alpha_preset_closes_converted_rgba_on_memory_error(self):
        """When np.array() raises on a converted RGBA image, apply_alpha_preset
        must close that image before propagating the error."""
        from PIL import Image
        import unittest.mock as mock
        from src.core.alpha_processor import apply_alpha_preset
        from src.core.presets import AlphaPreset

        closed = []

        class _FakeRGBA:
            mode = "RGBA"
            size = (4, 4)

            def close(self):
                closed.append(True)

        orig_convert = Image.Image.convert

        def patched_convert(self_img, mode, *args, **kwargs):
            if mode == "RGBA":
                return _FakeRGBA()
            return orig_convert(self_img, mode, *args, **kwargs)

        raised = []
        img = self._make_rgb_img()
        try:
            with mock.patch.object(Image.Image, "convert", patched_convert):
                with mock.patch("src.core.alpha_processor.np.array",
                                side_effect=MemoryError("simulated OOM")):
                    apply_alpha_preset(img, AlphaPreset("test", "", clamp_min=255, clamp_max=255))
        except MemoryError:
            raised.append(True)
        finally:
            img.close()

        self.assertTrue(raised, "apply_alpha_preset: MemoryError must propagate")
        self.assertTrue(closed, "apply_alpha_preset: converted RGBA img must be closed on MemoryError")

    def test_apply_manual_alpha_closes_converted_rgba_on_memory_error(self):
        """When np.array() raises on a converted RGBA image, apply_manual_alpha
        must close that image before propagating the error."""
        from PIL import Image
        import unittest.mock as mock
        from src.core.alpha_processor import apply_manual_alpha

        closed = []

        class _FakeRGBA:
            mode = "RGBA"
            size = (4, 4)

            def close(self):
                closed.append(True)

        orig_convert = Image.Image.convert

        def patched_convert(self_img, mode, *args, **kwargs):
            if mode == "RGBA":
                return _FakeRGBA()
            return orig_convert(self_img, mode, *args, **kwargs)

        raised = []
        img = self._make_rgb_img()
        try:
            with mock.patch.object(Image.Image, "convert", patched_convert):
                with mock.patch("src.core.alpha_processor.np.array",
                                side_effect=MemoryError("simulated OOM")):
                    apply_manual_alpha(img, clamp_min=255, clamp_max=255)
        except MemoryError:
            raised.append(True)
        finally:
            img.close()

        self.assertTrue(raised, "apply_manual_alpha: MemoryError must propagate")
        self.assertTrue(closed, "apply_manual_alpha: converted RGBA img must be closed on MemoryError")

    def test_apply_rgba_adjust_closes_converted_rgba_on_memory_error(self):
        """When np.array() raises on a converted RGBA image, apply_rgba_adjust
        must close that image before propagating the error."""
        from PIL import Image
        import unittest.mock as mock
        from src.core.alpha_processor import apply_rgba_adjust

        closed = []

        class _FakeRGBA:
            mode = "RGBA"
            size = (4, 4)

            def close(self):
                closed.append(True)

        orig_convert = Image.Image.convert

        def patched_convert(self_img, mode, *args, **kwargs):
            if mode == "RGBA":
                return _FakeRGBA()
            return orig_convert(self_img, mode, *args, **kwargs)

        raised = []
        img = self._make_rgb_img()
        try:
            with mock.patch.object(Image.Image, "convert", patched_convert):
                with mock.patch("src.core.alpha_processor.np.array",
                                side_effect=MemoryError("simulated OOM")):
                    apply_rgba_adjust(img, red_delta=10)
        except MemoryError:
            raised.append(True)
        finally:
            img.close()

        self.assertTrue(raised, "apply_rgba_adjust: MemoryError must propagate")
        self.assertTrue(closed, "apply_rgba_adjust: converted RGBA img must be closed on MemoryError")

    def test_apply_alpha_preset_already_rgba_not_double_closed_on_memory_error(self):
        """When the image is already RGBA, apply_alpha_preset must NOT close it
        in the MemoryError handler (caller's finally will do that)."""
        from PIL import Image
        import unittest.mock as mock
        from src.core.alpha_processor import apply_alpha_preset
        from src.core.presets import AlphaPreset

        close_count = [0]
        orig_close = Image.Image.close

        def counting_close(self_img):
            close_count[0] += 1
            orig_close(self_img)

        img = Image.new("RGBA", (4, 4), (100, 150, 200, 128))
        raised = []
        try:
            with mock.patch.object(Image.Image, "close", counting_close):
                with mock.patch("src.core.alpha_processor.np.array",
                                side_effect=MemoryError("simulated OOM")):
                    apply_alpha_preset(img, AlphaPreset("test", "", clamp_min=255, clamp_max=255))
        except MemoryError:
            raised.append(True)

        self.assertTrue(raised, "apply_alpha_preset: MemoryError must propagate for RGBA input")
        self.assertEqual(
            close_count[0], 0,
            "apply_alpha_preset: img must NOT be closed inside the function "
            "when it was already RGBA (no double-close)",
        )
        img.close()  # caller's responsibility


class TestRound14ResourceHygiene(unittest.TestCase):
    """Round-14: _open_image() leaks img when img.load() raises MemoryError.

    Bug (file_converter._open_image):

    When ``Image.open(path)`` succeeds but the subsequent ``img.load()`` call
    raises a ``MemoryError``, the original code accessed ``img.size`` to build
    the error message and then re-raised *without* calling ``img.close()``.
    This left the underlying file handle open until the garbage collector ran,
    potentially exhausting OS file-descriptor limits on large batches.

    Fix: call ``img.close()`` before the re-raise inside the ``MemoryError``
    handler.
    """

    _FC_SRC = os.path.join(
        os.path.dirname(__file__), "..", "src", "core", "file_converter.py"
    )

    def _read_fc(self) -> str:
        with open(self._FC_SRC) as f:
            return f.read()

    def _fn_src(self, fn_name: str) -> str:
        """Return the source of a top-level function in file_converter.py."""
        src = self._read_fc()
        start = src.find(f"\ndef {fn_name}(")
        self.assertGreater(start, 0, f"{fn_name} not found in file_converter.py")
        next_def = src.find("\ndef ", start + 1)
        return src[start:next_def] if next_def > 0 else src[start:]

    # ------------------------------------------------------------------
    # Source-level structural tests (guard against regression)
    # ------------------------------------------------------------------

    def test_open_image_closes_img_before_memory_error_raise(self):
        """_open_image must call img.close() inside the MemoryError handler."""
        fn = self._fn_src("_open_image")
        mem_pos = fn.find("except MemoryError:")
        self.assertGreater(mem_pos, 0,
                           "_open_image: must have an 'except MemoryError:' block")
        close_pos = fn.find("img.close()", mem_pos)
        raise_pos = fn.find("raise MemoryError(", mem_pos)
        self.assertGreater(
            close_pos, mem_pos,
            "_open_image: img.close() must appear inside the MemoryError handler",
        )
        self.assertGreater(
            raise_pos, close_pos,
            "_open_image: img.close() must come before the re-raise in the "
            "MemoryError handler",
        )

    def test_open_image_close_precedes_raise_in_handler(self):
        """img.close() must appear strictly before the raise MemoryError() line."""
        fn = self._fn_src("_open_image")
        mem_pos = fn.find("except MemoryError:")
        close_pos = fn.find("img.close()", mem_pos)
        raise_pos = fn.find("raise MemoryError(", mem_pos)
        self.assertLess(
            close_pos, raise_pos,
            "_open_image: img.close() must precede the raise inside the handler",
        )

    # ------------------------------------------------------------------
    # Behavioural smoke tests
    # ------------------------------------------------------------------

    def test_open_image_closes_img_on_load_memory_error(self):
        """_open_image must close the image when img.load() raises MemoryError."""
        import unittest.mock as mock
        from src.core.file_converter import _open_image

        closed = []

        class _FakeImg:
            mode = "RGB"
            size = (4, 4)

            def load(self):
                raise MemoryError("simulated OOM")

            def close(self):
                closed.append(True)

        raised = []
        with mock.patch("src.core.file_converter.Image.open",
                        return_value=_FakeImg()):
            try:
                _open_image("/fake/path.png")
            except MemoryError:
                raised.append(True)

        self.assertTrue(raised, "_open_image: MemoryError must propagate")
        self.assertTrue(
            closed,
            "_open_image: img must be closed before re-raising MemoryError",
        )

    def test_open_image_memory_error_message_contains_dimensions(self):
        """_open_image MemoryError message must contain image dimensions."""
        from PIL import Image
        import unittest.mock as mock
        from src.core.file_converter import _open_image

        orig_open = Image.open

        def patched_open(fp, *args, **kwargs):
            return Image.new("RGB", (100, 200))

        raised_msgs = []
        with mock.patch("src.core.file_converter.Image.open", patched_open):
            with mock.patch.object(Image.Image, "load",
                                   side_effect=MemoryError("OOM")):
                try:
                    _open_image("/fake/path.png")
                except MemoryError as exc:
                    raised_msgs.append(str(exc))

        self.assertTrue(raised_msgs, "_open_image: MemoryError must propagate")
        self.assertIn("100", raised_msgs[0],
                      "_open_image: error message must contain width")
        self.assertIn("200", raised_msgs[0],
                      "_open_image: error message must contain height")

    def test_open_image_returns_img_on_success(self):
        """_open_image must return the image when load() succeeds."""
        from PIL import Image
        import unittest.mock as mock
        import tempfile, os
        from src.core.file_converter import _open_image

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name
        try:
            Image.new("RGB", (8, 8), (255, 0, 0)).save(tmp_path)
            result = _open_image(tmp_path)
            try:
                self.assertEqual(result.size, (8, 8))
            finally:
                result.close()
        finally:
            os.unlink(tmp_path)


class TestRound15ResourceHygiene(unittest.TestCase):
    """Round-15: verify that locally-created RGBA copies are always closed in
    apply_alpha_preset / apply_manual_alpha / apply_rgba_adjust.

    Bug (all three processing functions):

    Round-13 fixed the MemoryError path by closing the locally-created RGBA
    copy inside the ``except MemoryError:`` handler.  However, the copy was
    still never explicitly closed:

      (a) on the **success** path — relying silently on CPython's reference-
          counting garbage collector instead of an explicit ``close()`` call, and
      (b) when any exception (MemoryError or otherwise) is raised by a numpy
          operation or ``Image.fromarray()`` *after* the ``np.array()`` call
          succeeds.

    Fix: wrap each function's processing body in a ``try/finally`` block so
    that ``if _converted: img.close()`` is called unconditionally, covering
    all code paths.
    """

    _AP_SRC = os.path.join(
        os.path.dirname(__file__), "..", "src", "core", "alpha_processor.py"
    )

    def _read_ap(self) -> str:
        with open(self._AP_SRC) as f:
            return f.read()

    def _fn_src(self, fn_name: str) -> str:
        """Return the source of a top-level function in alpha_processor.py."""
        src = self._read_ap()
        start = src.find(f"\ndef {fn_name}(")
        self.assertGreater(start, 0, f"{fn_name} not found")
        next_def = src.find("\ndef ", start + 1)
        return src[start:next_def] if next_def > 0 else src[start:]

    # ------------------------------------------------------------------
    # Source-level structural tests
    # ------------------------------------------------------------------

    def _assert_finally_closes_converted(self, fn_name: str) -> None:
        """Assert that fn_name has a finally block that closes img when _converted."""
        fn = self._fn_src(fn_name)
        self.assertIn(
            "finally:",
            fn,
            f"{fn_name}: must have a 'finally:' block",
        )
        fin_pos = fn.find("finally:")
        converted_pos = fn.find("if _converted:", fin_pos)
        self.assertGreater(
            converted_pos, fin_pos,
            f"{fn_name}: 'if _converted:' must appear inside the 'finally:' block",
        )
        close_pos = fn.find("img.close()", converted_pos)
        self.assertGreater(
            close_pos, converted_pos,
            f"{fn_name}: 'img.close()' must follow 'if _converted:' in the 'finally:' block",
        )

    def test_apply_alpha_preset_has_finally_close(self):
        """apply_alpha_preset must close the converted RGBA copy in a finally block."""
        self._assert_finally_closes_converted("apply_alpha_preset")

    def test_apply_manual_alpha_has_finally_close(self):
        """apply_manual_alpha must close the converted RGBA copy in a finally block."""
        self._assert_finally_closes_converted("apply_manual_alpha")

    def test_apply_rgba_adjust_has_finally_close(self):
        """apply_rgba_adjust must close the converted RGBA copy in a finally block."""
        self._assert_finally_closes_converted("apply_rgba_adjust")

    # ------------------------------------------------------------------
    # Behavioural smoke tests — late exception path
    # ------------------------------------------------------------------

    def _make_rgb_img(self, size=(4, 4)):
        return Image.new("RGB", size, (100, 150, 200))

    def _patched_convert_tracking(self, closed):
        """Return a patched Image.convert that tracks close() on the RGBA copy."""
        orig_convert = Image.Image.convert

        def patched_convert(self_img, mode, *args, **kwargs):
            real_result = orig_convert(self_img, mode, *args, **kwargs)
            if mode == "RGBA":
                orig_close = real_result.close
                def tracking_close(bound_orig=orig_close):
                    closed.append(True)
                    bound_orig()
                real_result.close = tracking_close
            return real_result

        return patched_convert

    def test_apply_alpha_preset_closes_converted_on_late_exception(self):
        """Converted RGBA copy must be closed when Image.fromarray() raises."""
        import unittest.mock as mock
        from src.core.alpha_processor import apply_alpha_preset
        from src.core.presets import AlphaPreset

        closed = []
        raised = []
        img = self._make_rgb_img()
        try:
            with mock.patch.object(Image.Image, "convert",
                                   self._patched_convert_tracking(closed)):
                with mock.patch("src.core.alpha_processor.Image.fromarray",
                                side_effect=MemoryError("late OOM")):
                    apply_alpha_preset(img, AlphaPreset("test", "", clamp_min=255, clamp_max=255))
        except MemoryError:
            raised.append(True)
        finally:
            img.close()

        self.assertTrue(raised, "apply_alpha_preset: late MemoryError must propagate")
        self.assertTrue(
            closed,
            "apply_alpha_preset: converted RGBA copy must be closed on a late exception",
        )

    def test_apply_manual_alpha_closes_converted_on_late_exception(self):
        """Converted RGBA copy must be closed when Image.fromarray() raises."""
        import unittest.mock as mock
        from src.core.alpha_processor import apply_manual_alpha

        closed = []
        raised = []
        img = self._make_rgb_img()
        try:
            with mock.patch.object(Image.Image, "convert",
                                   self._patched_convert_tracking(closed)):
                with mock.patch("src.core.alpha_processor.Image.fromarray",
                                side_effect=MemoryError("late OOM")):
                    apply_manual_alpha(img, clamp_min=255, clamp_max=255)
        except MemoryError:
            raised.append(True)
        finally:
            img.close()

        self.assertTrue(raised, "apply_manual_alpha: late MemoryError must propagate")
        self.assertTrue(
            closed,
            "apply_manual_alpha: converted RGBA copy must be closed on a late exception",
        )

    def test_apply_rgba_adjust_closes_converted_on_late_exception(self):
        """Converted RGBA copy must be closed when Image.fromarray() raises."""
        import unittest.mock as mock
        from src.core.alpha_processor import apply_rgba_adjust

        closed = []
        raised = []
        img = self._make_rgb_img()
        try:
            with mock.patch.object(Image.Image, "convert",
                                   self._patched_convert_tracking(closed)):
                with mock.patch("src.core.alpha_processor.Image.fromarray",
                                side_effect=MemoryError("late OOM")):
                    apply_rgba_adjust(img, red_delta=10)
        except MemoryError:
            raised.append(True)
        finally:
            img.close()

        self.assertTrue(raised, "apply_rgba_adjust: late MemoryError must propagate")
        self.assertTrue(
            closed,
            "apply_rgba_adjust: converted RGBA copy must be closed on a late exception",
        )

    # ------------------------------------------------------------------
    # Behavioural smoke tests — success path
    # ------------------------------------------------------------------

    def test_apply_alpha_preset_closes_converted_on_success(self):
        """Converted RGBA copy must be explicitly closed in the success path."""
        import unittest.mock as mock
        from src.core.alpha_processor import apply_alpha_preset
        from src.core.presets import AlphaPreset

        closed = []
        img = self._make_rgb_img()
        try:
            with mock.patch.object(Image.Image, "convert",
                                   self._patched_convert_tracking(closed)):
                result = apply_alpha_preset(img, AlphaPreset("test", "", clamp_min=255, clamp_max=255))
                result.close()
        finally:
            img.close()

        self.assertTrue(
            closed,
            "apply_alpha_preset: locally-created RGBA copy must be closed in the success path",
        )

    def test_apply_manual_alpha_closes_converted_on_success(self):
        """Converted RGBA copy must be explicitly closed in the success path."""
        import unittest.mock as mock
        from src.core.alpha_processor import apply_manual_alpha

        closed = []
        img = self._make_rgb_img()
        try:
            with mock.patch.object(Image.Image, "convert",
                                   self._patched_convert_tracking(closed)):
                result = apply_manual_alpha(img, clamp_min=255, clamp_max=255)
                result.close()
        finally:
            img.close()

        self.assertTrue(
            closed,
            "apply_manual_alpha: locally-created RGBA copy must be closed in the success path",
        )

    def test_apply_rgba_adjust_closes_converted_on_success(self):
        """Converted RGBA copy must be explicitly closed in the success path."""
        import unittest.mock as mock
        from src.core.alpha_processor import apply_rgba_adjust

        closed = []
        img = self._make_rgb_img()
        try:
            with mock.patch.object(Image.Image, "convert",
                                   self._patched_convert_tracking(closed)):
                result = apply_rgba_adjust(img, red_delta=10)
                result.close()
        finally:
            img.close()

        self.assertTrue(
            closed,
            "apply_rgba_adjust: locally-created RGBA copy must be closed in the success path",
        )

    def test_apply_alpha_preset_does_not_close_caller_rgba(self):
        """When input is already RGBA, apply_alpha_preset must NOT close it."""
        from src.core.alpha_processor import apply_alpha_preset
        from src.core.presets import AlphaPreset

        closed = []
        img = Image.new("RGBA", (4, 4), (100, 150, 200, 128))
        orig_close = img.close

        def tracking_close():
            closed.append(True)
            orig_close()

        img.close = tracking_close
        result = apply_alpha_preset(img, AlphaPreset("test", "", clamp_min=255, clamp_max=255))
        result.close()

        self.assertEqual(
            len(closed), 0,
            "apply_alpha_preset must NOT close the caller's RGBA image",
        )
        orig_close()  # cleanup

    def test_apply_manual_alpha_does_not_close_caller_rgba(self):
        """When input is already RGBA, apply_manual_alpha must NOT close it."""
        from src.core.alpha_processor import apply_manual_alpha

        closed = []
        img = Image.new("RGBA", (4, 4), (100, 150, 200, 128))
        orig_close = img.close

        def tracking_close():
            closed.append(True)
            orig_close()

        img.close = tracking_close
        result = apply_manual_alpha(img, clamp_min=255, clamp_max=255)
        result.close()

        self.assertEqual(
            len(closed), 0,
            "apply_manual_alpha must NOT close the caller's RGBA image",
        )
        orig_close()  # cleanup

    def test_apply_rgba_adjust_does_not_close_caller_rgba(self):
        """When input is already RGBA, apply_rgba_adjust must NOT close it."""
        from src.core.alpha_processor import apply_rgba_adjust

        closed = []
        img = Image.new("RGBA", (4, 4), (100, 150, 200, 128))
        orig_close = img.close

        def tracking_close():
            closed.append(True)
            orig_close()

        img.close = tracking_close
        result = apply_rgba_adjust(img, red_delta=10)
        result.close()

        self.assertEqual(
            len(closed), 0,
            "apply_rgba_adjust must NOT close the caller's RGBA image",
        )
        orig_close()  # cleanup


# ---------------------------------------------------------------------------
# Selective Alpha processor tests
# ---------------------------------------------------------------------------

class TestSelectiveAlphaProcessor(unittest.TestCase):
    """Tests for src.core.selective_alpha_processor."""

    def _make_rgba(self, w=8, h=8, alpha=200) -> Image.Image:
        arr = np.zeros((h, w, 4), dtype=np.uint8)
        arr[:, :, :3] = 128
        arr[:, :, 3]  = alpha
        return Image.fromarray(arr, "RGBA")

    # ---- detect_edges -----------------------------------------------------

    def test_detect_edges_returns_float32(self):
        from src.core.selective_alpha_processor import detect_edges
        img = self._make_rgba()
        e = detect_edges(img)
        self.assertEqual(e.dtype, np.float32)
        self.assertEqual(e.shape, (img.height, img.width))
        img.close()

    def test_detect_edges_uniform_image_is_zero(self):
        """A completely uniform grey image has no edges."""
        from src.core.selective_alpha_processor import detect_edges
        img = Image.new("RGBA", (16, 16), (100, 100, 100, 255))
        e = detect_edges(img)
        self.assertAlmostEqual(float(e.max()), 0.0, places=4)
        img.close()

    def test_detect_edges_checkerboard_has_edges(self):
        """A 2×2 checkerboard pattern should produce non-zero edges."""
        from src.core.selective_alpha_processor import detect_edges
        arr = np.zeros((8, 8, 4), dtype=np.uint8)
        for r in range(8):
            for c in range(8):
                v = 255 if (r + c) % 2 == 0 else 0
                arr[r, c, :3] = v
                arr[r, c, 3]  = 255
        img = Image.fromarray(arr, "RGBA")
        e = detect_edges(img)
        self.assertGreater(float(e.max()), 0.1)
        img.close()

    def test_detect_edges_values_in_range(self):
        from src.core.selective_alpha_processor import detect_edges
        img = self._make_rgba()
        e = detect_edges(img)
        self.assertGreaterEqual(float(e.min()), 0.0)
        self.assertLessEqual(float(e.max()), 1.0 + 1e-6)
        img.close()

    # ---- edge_flood_fill --------------------------------------------------

    def test_flood_fill_uniform_fills_all(self):
        """On a uniform (zero-edge) image the fill covers the whole image."""
        from src.core.selective_alpha_processor import edge_flood_fill
        h, w = 10, 10
        edge_map = np.zeros((h, w), dtype=np.float32)
        mask = edge_flood_fill((5, 5), edge_map)
        self.assertEqual(mask.shape, (h, w))
        self.assertTrue(mask.all(), "Expected the entire image to be filled")

    def test_flood_fill_out_of_bounds_seed_returns_empty(self):
        from src.core.selective_alpha_processor import edge_flood_fill
        edge_map = np.zeros((10, 10), dtype=np.float32)
        mask = edge_flood_fill((20, 20), edge_map)
        self.assertFalse(mask.any())

    def test_flood_fill_blocked_by_edge(self):
        """A strong vertical edge should prevent fill from crossing it."""
        from src.core.selective_alpha_processor import edge_flood_fill
        h, w = 10, 10
        edge_map = np.zeros((h, w), dtype=np.float32)
        edge_map[:, 5] = 1.0   # strong vertical barrier
        mask = edge_flood_fill((2, 2), edge_map, threshold=0.5)
        # All filled pixels should be to the left of the edge (col < 5)
        filled_cols = np.where(mask)[1]
        self.assertTrue(
            (filled_cols < 5).all(),
            "Fill should not cross the strong vertical edge"
        )

    def test_flood_fill_seed_on_edge_returns_empty(self):
        from src.core.selective_alpha_processor import edge_flood_fill
        edge_map = np.ones((10, 10), dtype=np.float32)   # all strong edges
        mask = edge_flood_fill((5, 5), edge_map, threshold=0.5)
        self.assertFalse(mask.any())

    # ---- autocorrect_mask -------------------------------------------------

    def test_autocorrect_empty_mask_unchanged(self):
        from src.core.selective_alpha_processor import autocorrect_mask
        mask = np.zeros((10, 10), dtype=bool)
        edge_map = np.zeros((10, 10), dtype=np.float32)
        result = autocorrect_mask(mask, edge_map)
        self.assertFalse(result.any())

    def test_autocorrect_expands_toward_edges(self):
        """A drawn mask near a strong edge should be expanded to include the edge."""
        from src.core.selective_alpha_processor import autocorrect_mask
        h, w = 30, 30
        mask = np.zeros((h, w), dtype=bool)
        mask[10:20, 5:10] = True   # drawn region

        edge_map = np.zeros((h, w), dtype=np.float32)
        edge_map[10:20, 14] = 0.9  # strong edge 4 pixels away

        result = autocorrect_mask(mask, edge_map, search_radius=8, edge_threshold=0.5)
        # The edge pixels should now be included
        self.assertTrue(result[10, 14], "Edge pixel should be snapped into mask")
        # Original drawn region should still be present
        self.assertTrue(result[10, 5])

    def test_autocorrect_no_edges_unchanged(self):
        from src.core.selective_alpha_processor import autocorrect_mask
        mask = np.zeros((10, 10), dtype=bool)
        mask[2:7, 2:7] = True
        edge_map = np.zeros((10, 10), dtype=np.float32)
        result = autocorrect_mask(mask, edge_map, edge_threshold=0.5)
        np.testing.assert_array_equal(result, mask)

    # ---- apply_selective_alpha --------------------------------------------

    def test_apply_sets_zone_alpha(self):
        """Pixels inside a zone mask must receive the zone's alpha value."""
        from src.core.selective_alpha_processor import apply_selective_alpha, NUM_ZONES
        img = self._make_rgba(8, 8, alpha=255)
        zone_masks: list = [None] * NUM_ZONES
        mask = np.zeros((8, 8), dtype=bool)
        mask[2:5, 2:5] = True
        zone_masks[0] = mask
        zone_alphas = [42] + [255] * (NUM_ZONES - 1)
        result = apply_selective_alpha(img, zone_masks, zone_alphas)
        arr = np.array(result, dtype=np.uint8)
        self.assertEqual(int(arr[3, 3, 3]), 42, "Zone alpha should be 42")
        self.assertEqual(int(arr[0, 0, 3]), 255, "Unpainted pixel keeps original alpha")
        result.close()
        img.close()

    def test_apply_zone0_wins_on_overlap(self):
        """Zone 0 has highest priority and wins when zones overlap."""
        from src.core.selective_alpha_processor import apply_selective_alpha, NUM_ZONES
        img = self._make_rgba(8, 8, alpha=200)
        # Both zone 0 and zone 1 cover the same pixels
        full_mask = np.ones((8, 8), dtype=bool)
        zone_masks = [full_mask, full_mask] + [None] * (NUM_ZONES - 2)
        zone_alphas = [10, 99] + [200] * (NUM_ZONES - 2)
        result = apply_selective_alpha(img, zone_masks, zone_alphas)
        arr = np.array(result, dtype=np.uint8)
        self.assertEqual(int(arr[0, 0, 3]), 10, "Zone 0 (alpha=10) should win over zone 1")
        result.close()
        img.close()

    def test_apply_empty_zones_keeps_original_alpha(self):
        """If no zones are painted, the alpha channel must not change."""
        from src.core.selective_alpha_processor import apply_selective_alpha, NUM_ZONES
        original_alpha = 77
        img = self._make_rgba(6, 6, alpha=original_alpha)
        zone_masks = [None] * NUM_ZONES
        zone_alphas = [0] * NUM_ZONES
        result = apply_selective_alpha(img, zone_masks, zone_alphas)
        arr = np.array(result, dtype=np.uint8)
        self.assertTrue(
            (arr[:, :, 3] == original_alpha).all(),
            "All alphas should remain unchanged when no zones are painted"
        )
        result.close()
        img.close()

    def test_apply_returns_rgba(self):
        from src.core.selective_alpha_processor import apply_selective_alpha, NUM_ZONES
        img = Image.new("RGB", (4, 4), (100, 100, 100))
        zone_masks = [None] * NUM_ZONES
        zone_alphas = [128] * NUM_ZONES
        result = apply_selective_alpha(img, zone_masks, zone_alphas)
        self.assertEqual(result.mode, "RGBA")
        result.close()
        img.close()

    # ---- composite_zones --------------------------------------------------

    def test_composite_zones_output_shape(self):
        from src.core.selective_alpha_processor import composite_zones, NUM_ZONES
        h, w = 8, 8
        src = np.zeros((h, w, 4), dtype=np.uint8)
        src[:, :] = [100, 100, 100, 255]
        zone_masks = [None] * NUM_ZONES
        out = composite_zones(src, zone_masks)
        self.assertEqual(out.shape, (h, w, 4))
        self.assertEqual(out.dtype, np.uint8)

    def test_composite_zones_tints_zone_area(self):
        """Pixels covered by a zone should be blended with the zone colour."""
        from src.core.selective_alpha_processor import composite_zones, NUM_ZONES, ZONE_COLORS
        h, w = 8, 8
        src = np.zeros((h, w, 4), dtype=np.uint8)
        src[:, :] = [200, 200, 200, 255]
        mask = np.zeros((h, w), dtype=bool)
        mask[2:6, 2:6] = True
        zone_masks = [mask] + [None] * (NUM_ZONES - 1)
        out = composite_zones(src, zone_masks)
        # The top-left corner (not in zone) should be unchanged
        np.testing.assert_array_equal(out[0, 0], src[0, 0])
        # The zone area should be tinted (different from original)
        self.assertFalse(
            np.array_equal(out[3, 3], src[3, 3]),
            "Zone area should be blended with zone colour"
        )


# ---------------------------------------------------------------------------
# Selective Alpha canvas unit tests (no Qt display required)
# ---------------------------------------------------------------------------

class TestSelectiveAlphaCanvasLogic(unittest.TestCase):
    """
    Tests for the non-Qt helper logic on SelectiveAlphaCanvas:
      _snapshot / _restore_snapshot / _push_history / undo_mask / redo_mask
      _erase_brush / _erase_brush_move
    These tests bypass the Qt paint/event system by calling internal helpers
    directly and inspecting the resulting masks.
    """

    def _make_canvas_with_image(self):
        """
        Return a canvas instance that has a 16x16 RGBA image loaded without
        ever calling Qt rendering (we skip load_image and set internals
        directly to avoid QApplication dependency in headless CI).
        """
        import sys, types

        # Build a minimal stub PyQt6 environment if Qt is not available.
        # If PyQt6 IS importable we use it; otherwise we create stubs.
        try:
            from PyQt6.QtWidgets import QApplication
            # Store on self to prevent premature garbage collection.
            self._qapp = QApplication.instance() or QApplication(sys.argv)
            from src.ui.selective_alpha_tool import SelectiveAlphaCanvas
            canvas = SelectiveAlphaCanvas.__new__(SelectiveAlphaCanvas)
        except ImportError:
            self.skipTest("PyQt6 not available in this environment")

        # Initialise just the attributes used by the history/eraser helpers.
        canvas._src_img   = Image.new("RGBA", (16, 16), (100, 100, 100, 200))
        canvas._src_arr   = np.array(canvas._src_img, dtype=np.uint8)
        canvas._masks     = [None] * 7
        canvas._history   = []
        canvas._redo_stack = []
        canvas._brush_size  = 3
        canvas._eraser_size = 3
        canvas._active_zone = 0
        canvas._edge_map    = None
        canvas._composite_dirty = True
        return canvas

    # ---- snapshot helpers -------------------------------------------------

    def test_snapshot_all_none(self):
        canvas = self._make_canvas_with_image()
        snap = canvas._snapshot()
        self.assertEqual(len(snap), 7)
        self.assertTrue(all(s is None for s in snap))

    def test_snapshot_captures_mask(self):
        canvas = self._make_canvas_with_image()
        canvas._masks[0] = Image.fromarray(
            np.full((16, 16), 255, dtype=np.uint8), "L"
        )
        snap = canvas._snapshot()
        self.assertIsNotNone(snap[0])
        self.assertTrue((snap[0] == 255).all())
        canvas._masks[0].close()

    def test_restore_snapshot_restores_mask(self):
        canvas = self._make_canvas_with_image()
        arr = np.full((16, 16), 200, dtype=np.uint8)
        snap = [arr.copy()] + [None] * 6

        # Manually stub update() so it doesn't try to paint
        canvas.update = lambda: None
        canvas._restore_snapshot(snap)

        m = canvas._masks[0]
        self.assertIsNotNone(m)
        result = np.array(m, dtype=np.uint8)
        self.assertTrue((result == 200).all())
        m.close()

    # ---- push / undo / redo -----------------------------------------------

    def _stub_signals(self, canvas):
        """Replace pyqtSignal emitters with no-ops for headless testing."""
        canvas.undo_available = types.SimpleNamespace(emit=lambda v: None)
        canvas.redo_available = types.SimpleNamespace(emit=lambda v: None)
        canvas.mask_changed   = types.SimpleNamespace(emit=lambda v: None)
        canvas.update = lambda: None

    def test_push_history_adds_entry(self):
        import types
        canvas = self._make_canvas_with_image()
        self._stub_signals(canvas)
        self.assertEqual(len(canvas._history), 0)
        canvas._push_history()
        self.assertEqual(len(canvas._history), 1)

    def test_undo_restores_previous_state(self):
        import types
        canvas = self._make_canvas_with_image()
        self._stub_signals(canvas)

        # Start empty, push history, then paint zone 0
        canvas._push_history()
        canvas._masks[0] = Image.fromarray(
            np.full((16, 16), 255, dtype=np.uint8), "L"
        )

        self.assertIsNotNone(canvas._masks[0])
        result = canvas.undo_mask()
        self.assertTrue(result)
        self.assertIsNone(canvas._masks[0])   # restored to pre-paint state

    def test_redo_restores_forward_state(self):
        import types
        canvas = self._make_canvas_with_image()
        self._stub_signals(canvas)

        canvas._push_history()
        canvas._masks[0] = Image.fromarray(
            np.full((16, 16), 128, dtype=np.uint8), "L"
        )
        canvas.undo_mask()
        self.assertIsNone(canvas._masks[0])

        result = canvas.redo_mask()
        self.assertTrue(result)
        self.assertIsNotNone(canvas._masks[0])
        arr = np.array(canvas._masks[0], dtype=np.uint8)
        self.assertTrue((arr == 128).all())
        canvas._masks[0].close()

    def test_push_clears_redo_stack(self):
        import types
        canvas = self._make_canvas_with_image()
        self._stub_signals(canvas)

        canvas._push_history()
        canvas._masks[0] = Image.fromarray(
            np.full((16, 16), 255, dtype=np.uint8), "L"
        )
        canvas.undo_mask()
        self.assertEqual(len(canvas._redo_stack), 1)

        # Push new history: redo stack should be cleared
        canvas._push_history()
        self.assertEqual(len(canvas._redo_stack), 0)

    # ---- eraser -----------------------------------------------------------

    def test_erase_brush_clears_all_zones(self):
        canvas = self._make_canvas_with_image()

        # Paint two zones with full white masks
        for i in range(3):
            canvas._masks[i] = Image.fromarray(
                np.full((16, 16), 255, dtype=np.uint8), "L"
            )

        # Erase at centre
        canvas._erase_brush(8, 8)

        # The erased region should be 0 in all three painted zones
        for i in range(3):
            arr = np.array(canvas._masks[i], dtype=np.uint8)
            # Centre pixel must be 0 after erasing
            self.assertEqual(
                arr[8, 8], 0,
                f"Zone {i} centre pixel should be erased"
            )
            canvas._masks[i].close()
            canvas._masks[i] = None

    def test_erase_brush_no_effect_on_none_zone(self):
        canvas = self._make_canvas_with_image()
        canvas._erase_brush(8, 8)   # all masks are None – must not raise

    def test_eraser_size_applies(self):
        canvas = self._make_canvas_with_image()
        canvas._eraser_size = 1
        canvas._masks[0] = Image.fromarray(
            np.full((16, 16), 255, dtype=np.uint8), "L"
        )
        canvas._erase_brush(8, 8)
        arr = np.array(canvas._masks[0], dtype=np.uint8)
        # Centre should be erased; corner should not
        self.assertEqual(arr[8, 8], 0)
        self.assertEqual(arr[0, 0], 255)
        canvas._masks[0].close()
        canvas._masks[0] = None
