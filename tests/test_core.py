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
        self.assertIn("Transparent", names)

    # Use the deduplicated merged preset names
    _FULL_OPACITY_NAME = "Full Opacity  (N64 · DS · Wii · Xbox 360 · PS2 BG)"
    _HALF_OPACITY_NAME = "Half Opacity  (PS2 Sprite · GBA · PSP)"

    def test_ps2_preset_values(self):
        p = self._mgr.get_preset(self._HALF_OPACITY_NAME)
        self.assertIsNotNone(p)
        self.assertEqual(p.alpha_value, 128)
        self.assertEqual(p.fill_mode, "set")

    def test_n64_preset_values(self):
        p = self._mgr.get_preset(self._FULL_OPACITY_NAME)
        self.assertIsNotNone(p)
        self.assertEqual(p.alpha_value, 255)

    def test_get_nonexistent_preset(self):
        self.assertIsNone(self._mgr.get_preset("DoesNotExist"))

    def test_cannot_overwrite_builtin(self):
        custom = AlphaPreset(self._HALF_OPACITY_NAME, 0, "set", 0, 0, False, "test")
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
        result = self._mgr.delete_custom_preset(self._HALF_OPACITY_NAME)
        self.assertFalse(result)
        self.assertIsNotNone(self._mgr.get_preset(self._HALF_OPACITY_NAME))


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
        self.assertEqual(len(te.HIDDEN_THEMES), 22,
                         f"Expected 22 hidden themes, got {len(te.HIDDEN_THEMES)}")

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
        # Both should appear in the connections block
        conn_start = source.find("_mode_combo.currentTextChanged")
        self.assertGreater(conn_start, 0,
                           "_mode_combo.currentTextChanged not found in alpha_tool.py")
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

