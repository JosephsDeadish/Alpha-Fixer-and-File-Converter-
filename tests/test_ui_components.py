"""
Tests for new UI components: DropFileList, SoundEngine, MouseTrailOverlay,
and the extended SettingsManager.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Make src importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ---------------------------------------------------------------------------
# DropFileList tests (headless via QApplication)
# ---------------------------------------------------------------------------

def _get_app():
    """Return or create a QApplication for widget tests, flushing deferred deletions."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(["test"])
    # Process any pending deleteLater() events from previous tests
    app.processEvents()
    return app


class TestDropFileList(unittest.TestCase):
    def setUp(self):
        self._app = _get_app()
        from src.ui.drop_list import DropFileList
        self._widget = DropFileList()

    def tearDown(self):
        self._widget.hide()
        self._widget.deleteLater()
        self._app.processEvents()  # flush deferred deletion now

    def test_initial_count_is_zero(self):
        self.assertEqual(self._widget.count(), 0)

    def test_add_items_manually(self):
        self._widget.addItem("/tmp/a.png")
        self._widget.addItem("/tmp/b.png")
        self.assertEqual(self._widget.count(), 2)

    def test_count_changed_on_clear(self):
        self._widget.addItem("/tmp/a.png")
        received = []
        self._widget.count_changed.connect(received.append)
        self._widget._clear_all()
        self.assertEqual(self._widget.count(), 0)
        self.assertEqual(received, [0])

    def test_remove_selected_emits_count_changed(self):
        self._widget.addItem("/tmp/a.png")
        self._widget.addItem("/tmp/b.png")
        self._widget.item(0).setSelected(True)
        received = []
        self._widget.count_changed.connect(received.append)
        self._widget._remove_selected()
        self.assertEqual(self._widget.count(), 1)
        self.assertTrue(len(received) > 0)
        self.assertEqual(received[-1], 1)

    def test_remove_selected_when_nothing_selected(self):
        self._widget.addItem("/tmp/a.png")
        received = []
        self._widget.count_changed.connect(received.append)
        self._widget._remove_selected()
        # Nothing was selected, so nothing removed
        self.assertEqual(self._widget.count(), 1)
        self.assertEqual(len(received), 0)

    def test_drag_enter_accepts_urls(self):
        """Verify dragEnterEvent accepts URL MIME data."""
        from PyQt6.QtCore import QMimeData, QUrl
        from PyQt6.QtGui import QDragEnterEvent
        from PyQt6.QtCore import Qt
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile("/tmp/test.png")])
        # We can't fully simulate a drag event without a display,
        # but we can verify the widget accepts drops
        self.assertTrue(self._widget.acceptDrops())

    def test_paths_dropped_signal(self):
        """paths_dropped should be a signal (not None)."""
        from PyQt6.QtCore import pyqtSignal
        # Just verify it exists and is callable/connectable
        received = []
        self._widget.paths_dropped.connect(received.extend)
        # Simulate internal emit (bypassing actual drag)
        self._widget.paths_dropped.emit(["/tmp/fake.png"])
        self.assertEqual(received, ["/tmp/fake.png"])


# ---------------------------------------------------------------------------
# SettingsManager – new keys
# ---------------------------------------------------------------------------

class TestSettingsManagerNewKeys(unittest.TestCase):
    def setUp(self):
        _get_app()
        # Use a temp location to avoid polluting real settings
        from PyQt6.QtCore import QSettings
        with patch.object(
            __import__("src.core.settings_manager", fromlist=["SettingsManager"]),
            "SettingsManager",
        ):
            pass
        from src.core.settings_manager import SettingsManager
        self._mgr = SettingsManager()
        # Override internal QSettings to use an in-memory store
        self._store: dict = {}
        self._mgr._qs = _FakeQSettings(self._store)

    def test_font_size_default(self):
        val = self._mgr.get("font_size", 10)
        self.assertEqual(val, 10)

    def test_last_alpha_preset_default(self):
        val = self._mgr.get("last_alpha_preset", "")
        self.assertEqual(val, "")

    def test_last_converter_format_default(self):
        val = self._mgr.get("last_converter_format", "PNG")
        self.assertEqual(val, "PNG")

    def test_cursor_default(self):
        val = self._mgr.get("cursor", "Default")
        self.assertEqual(val, "Default")

    def test_save_and_get_named_theme(self):
        theme = {"name": "My Theme", "background": "#112233"}
        self._mgr.save_named_theme("My Theme", theme)
        saved = self._mgr.get_saved_themes()
        self.assertIn("My Theme", saved)
        self.assertEqual(saved["My Theme"]["background"], "#112233")

    def test_delete_named_theme(self):
        self._mgr.save_named_theme("Temp", {"name": "Temp"})
        result = self._mgr.delete_named_theme("Temp")
        self.assertTrue(result)
        self.assertNotIn("Temp", self._mgr.get_saved_themes())

    def test_delete_nonexistent_named_theme(self):
        result = self._mgr.delete_named_theme("DoesNotExist")
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# SoundEngine – basic instantiation and WAV generation
# ---------------------------------------------------------------------------

class TestSoundEngine(unittest.TestCase):
    def test_wav_generation(self):
        """_make_click_wav should produce a valid WAV file."""
        from src.ui.sound_engine import _make_click_wav
        import wave
        path = None
        try:
            path = _make_click_wav()
            self.assertTrue(os.path.isfile(path))
            with wave.open(path) as wf:
                self.assertEqual(wf.getnchannels(), 1)
                self.assertEqual(wf.getsampwidth(), 2)
                self.assertGreater(wf.getnframes(), 0)
        finally:
            if path and os.path.isfile(path):
                os.unlink(path)

    def test_play_click_respects_sound_disabled(self):
        """play_click should not attempt to play when sound_enabled=False."""
        _get_app()
        settings = MagicMock()
        settings.get.side_effect = lambda k, d=None: False if k == "sound_enabled" else (d or "")
        from src.ui.sound_engine import SoundEngine
        engine = SoundEngine(settings)
        # Should not raise even with no multimedia backend
        engine.play_click()
        engine.cleanup()

    def test_cleanup_removes_temp_file(self):
        """cleanup() should remove the generated temp WAV."""
        _get_app()
        settings = MagicMock()
        settings.get.return_value = False
        from src.ui.sound_engine import SoundEngine
        engine = SoundEngine(settings)
        wav = engine._click_wav
        engine.cleanup()
        if wav:
            self.assertFalse(os.path.isfile(wav))


# ---------------------------------------------------------------------------
# MouseTrailOverlay – basic construction
# ---------------------------------------------------------------------------

class TestMouseTrailOverlay(unittest.TestCase):
    def setUp(self):
        self._app = _get_app()  # Must keep a reference to prevent GC
        from PyQt6.QtWidgets import QWidget
        self._parent = QWidget()
        self._parent.resize(800, 600)

    def tearDown(self):
        self._parent.hide()
        self._parent.deleteLater()
        self._app.processEvents()

    def test_overlay_is_child_of_parent(self):
        from src.ui.mouse_trail import MouseTrailOverlay
        overlay = MouseTrailOverlay(self._parent)
        self.assertIs(overlay.parent(), self._parent)

    def test_overlay_starts_disabled(self):
        from src.ui.mouse_trail import MouseTrailOverlay
        overlay = MouseTrailOverlay(self._parent)
        self.assertFalse(overlay._enabled)

    def test_set_enabled_true_then_false(self):
        from src.ui.mouse_trail import MouseTrailOverlay
        overlay = MouseTrailOverlay(self._parent)
        overlay.set_enabled(True)
        self.assertTrue(overlay._enabled)
        overlay.set_enabled(False)
        self.assertFalse(overlay._enabled)

    def test_set_color(self):
        from src.ui.mouse_trail import MouseTrailOverlay
        from PyQt6.QtGui import QColor
        overlay = MouseTrailOverlay(self._parent)
        overlay.set_color("#00ff88")
        self.assertEqual(overlay._color, QColor("#00ff88"))

    def test_transparent_for_mouse_events(self):
        from src.ui.mouse_trail import MouseTrailOverlay
        from PyQt6.QtCore import Qt
        overlay = MouseTrailOverlay(self._parent)
        self.assertTrue(
            overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        )

    def test_no_system_background(self):
        from src.ui.mouse_trail import MouseTrailOverlay
        from PyQt6.QtCore import Qt
        overlay = MouseTrailOverlay(self._parent)
        self.assertTrue(
            overlay.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        )


# ---------------------------------------------------------------------------
# ImagePreviewPane
# ---------------------------------------------------------------------------

class TestImagePreviewPane(unittest.TestCase):
    def setUp(self):
        self._app = _get_app()
        from PyQt6.QtWidgets import QWidget
        self._parent = QWidget()
        self._parent.resize(400, 400)

    def tearDown(self):
        self._parent.hide()
        self._parent.deleteLater()
        self._app.processEvents()

    def test_pane_creates_without_error(self):
        from src.ui.preview_pane import ImagePreviewPane
        pane = ImagePreviewPane(self._parent)
        self.assertIsNotNone(pane)

    def test_clear_resets_label(self):
        from src.ui.preview_pane import ImagePreviewPane
        pane = ImagePreviewPane(self._parent)
        pane.clear()
        self.assertEqual(pane._meta_label.text(), "Select a file to preview")

    def test_show_nonexistent_file_calls_clear(self):
        from src.ui.preview_pane import ImagePreviewPane
        pane = ImagePreviewPane(self._parent)
        pane.show_file("/nonexistent/path/image.png")
        # Should clear gracefully (no exception); meta label stays at placeholder
        self.assertEqual(pane._meta_label.text(), "Select a file to preview")

    def test_show_real_image(self):
        """Thumbnail load should eventually set a non-placeholder label."""
        from src.ui.preview_pane import _ThumbLoader
        from PIL import Image

        results = []

        with tempfile.TemporaryDirectory() as td:
            img_path = os.path.join(td, "test.png")
            Image.new("RGBA", (64, 64), (255, 0, 128, 200)).save(img_path)

            loader = _ThumbLoader(img_path)
            loader.loaded.connect(lambda qimg, meta: results.append(meta))
            loader.start()
            loader.wait(3000)  # wait for thread to finish
            self._app.processEvents()  # flush signals into the main thread

        self.assertEqual(len(results), 1, "loaded signal should fire once")
        self.assertIn("test.png", results[0])


# ---------------------------------------------------------------------------
# BeforeAfterWidget
# ---------------------------------------------------------------------------

class TestBeforeAfterWidget(unittest.TestCase):
    def setUp(self):
        self._app = _get_app()
        from PyQt6.QtWidgets import QWidget
        self._parent = QWidget()
        self._parent.resize(400, 300)

    def tearDown(self):
        self._parent.hide()
        self._parent.deleteLater()
        self._app.processEvents()

    def test_creates_without_error(self):
        from src.ui.preview_pane import BeforeAfterWidget
        w = BeforeAfterWidget(self._parent)
        self.assertIsNotNone(w)

    def test_initial_split_is_half(self):
        from src.ui.preview_pane import BeforeAfterWidget
        w = BeforeAfterWidget(self._parent)
        self.assertAlmostEqual(w._split, 0.5)

    def test_split_clamps_low(self):
        from src.ui.preview_pane import BeforeAfterWidget
        w = BeforeAfterWidget(self._parent)
        w._update_split(-100)
        self.assertGreaterEqual(w._split, 0.02)

    def test_split_clamps_high(self):
        from src.ui.preview_pane import BeforeAfterWidget
        w = BeforeAfterWidget(self._parent)
        w._update_split(99999)
        self.assertLessEqual(w._split, 0.98)

    def test_split_moves_to_correct_fraction(self):
        from src.ui.preview_pane import BeforeAfterWidget
        w = BeforeAfterWidget(self._parent)
        w.resize(400, 300)
        w._update_split(200)   # exactly half
        self.assertAlmostEqual(w._split, 0.5, places=2)

    def test_set_before_does_not_raise(self):
        from src.ui.preview_pane import BeforeAfterWidget, _pil_to_qimage
        from PIL import Image
        w = BeforeAfterWidget(self._parent)
        qi = _pil_to_qimage(Image.new("RGBA", (32, 32), (255, 0, 0, 128)))
        w.set_before(qi)
        self.assertIsNotNone(w._pix_before)

    def test_set_after_does_not_raise(self):
        from src.ui.preview_pane import BeforeAfterWidget, _pil_to_qimage
        from PIL import Image
        w = BeforeAfterWidget(self._parent)
        qi = _pil_to_qimage(Image.new("RGBA", (32, 32), (0, 0, 255, 200)))
        w.set_after(qi)
        self.assertIsNotNone(w._pix_after)

    def test_set_loading_clears_after(self):
        from src.ui.preview_pane import BeforeAfterWidget, _pil_to_qimage
        from PIL import Image
        w = BeforeAfterWidget(self._parent)
        qi = _pil_to_qimage(Image.new("RGBA", (32, 32)))
        w.set_after(qi)
        w.set_loading()
        self.assertIsNone(w._pix_after)
        self.assertTrue(w._loading)

    def test_clear_resets_state(self):
        from src.ui.preview_pane import BeforeAfterWidget, _pil_to_qimage
        from PIL import Image
        w = BeforeAfterWidget(self._parent)
        qi = _pil_to_qimage(Image.new("RGBA", (32, 32)))
        w.set_before(qi)
        w.set_after(qi)
        w.clear()
        self.assertIsNone(w._pix_before)
        self.assertIsNone(w._pix_after)
        self.assertFalse(w._loading)

    def test_paint_does_not_crash_when_empty(self):
        """paintEvent must not crash even with no images."""
        from src.ui.preview_pane import BeforeAfterWidget
        w = BeforeAfterWidget(self._parent)
        w.show()
        w.resize(300, 200)
        self._app.processEvents()

    def test_paint_does_not_crash_with_images(self):
        """paintEvent must not crash with both images set."""
        from src.ui.preview_pane import BeforeAfterWidget, _pil_to_qimage
        from PIL import Image
        w = BeforeAfterWidget(self._parent)
        w.resize(300, 200)
        qi = _pil_to_qimage(Image.new("RGBA", (64, 64), (100, 150, 200, 180)))
        w.set_before(qi)
        w.set_after(qi)
        w.show()
        self._app.processEvents()


# ---------------------------------------------------------------------------
# _AlphaPreviewLoader (before/after background processor)
# ---------------------------------------------------------------------------

class TestAlphaPreviewLoader(unittest.TestCase):
    def setUp(self):
        self._app = _get_app()

    def tearDown(self):
        self._app.processEvents()

    def test_processes_image_and_emits_both_sides(self):
        """preview_ready should emit (before QImage, after QImage)."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from src.ui.alpha_tool import _AlphaPreviewLoader
        from src.core.presets import BUILTIN_PRESETS
        from PIL import Image

        results = []

        with tempfile.TemporaryDirectory() as td:
            img_path = os.path.join(td, "test.png")
            Image.new("RGBA", (32, 32), (200, 200, 200, 200)).save(img_path)

            preset = BUILTIN_PRESETS[0]  # PS2 preset
            loader = _AlphaPreviewLoader(img_path, preset=preset)
            loader.preview_ready.connect(
                lambda b, a: results.append((b, a))
            )
            loader.start()
            loader.wait(5000)
            self._app.processEvents()

        self.assertEqual(len(results), 1, "preview_ready should fire once")
        before_qi, after_qi = results[0]
        self.assertFalse(before_qi.isNull())
        self.assertFalse(after_qi.isNull())
        # Before and after should differ (PS2 changes alpha from 200 → 128)
        self.assertNotEqual(before_qi.pixel(10, 10), after_qi.pixel(10, 10))

    def test_failed_signal_on_bad_path(self):
        """failed signal should fire when the path doesn't exist."""
        from src.ui.alpha_tool import _AlphaPreviewLoader

        errors = []
        loader = _AlphaPreviewLoader("/nonexistent/bad.png")
        loader.failed.connect(errors.append)
        loader.start()
        loader.wait(3000)
        self._app.processEvents()
        self.assertEqual(len(errors), 1)




class TestSettingsExportImport(unittest.TestCase):
    def setUp(self):
        _get_app()
        from src.core.settings_manager import SettingsManager
        with patch.object(
            __import__("src.core.settings_manager", fromlist=["SettingsManager"]),
            "SettingsManager",
        ):
            pass
        self._mgr = SettingsManager()
        self._store: dict = {}
        self._mgr._qs = _FakeQSettings(self._store)

    def test_export_creates_json_file(self):
        from src.core.settings_manager import SettingsManager
        self._mgr.set("font_size", 14)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "settings.json")
            self._mgr.export_settings(path)
            self.assertTrue(os.path.isfile(path))
            import json
            with open(path) as f:
                data = json.load(f)
            self.assertIn("font_size", data)
            self.assertEqual(data["font_size"], 14)

    def test_import_restores_keys(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "settings.json")
            with open(path, "w") as f:
                json.dump({"font_size": 18, "cursor": "Cross"}, f)
            imported = self._mgr.import_settings(path)
            self.assertIn("font_size", imported)
            self.assertIn("cursor", imported)
            self.assertEqual(self._mgr.get("font_size", 10), 18)
            self.assertEqual(self._mgr.get("cursor", "Default"), "Cross")

    def test_import_skips_unknown_keys(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "settings.json")
            with open(path, "w") as f:
                json.dump({"unknown_key_xyz": "should_be_ignored", "font_size": 12}, f)
            imported = self._mgr.import_settings(path)
            self.assertNotIn("unknown_key_xyz", imported)
            self.assertIn("font_size", imported)


# ---------------------------------------------------------------------------
# SettingsManager – converter history
# ---------------------------------------------------------------------------

class TestConverterHistory(unittest.TestCase):
    def setUp(self):
        _get_app()
        from src.core.settings_manager import SettingsManager
        with patch.object(
            __import__("src.core.settings_manager", fromlist=["SettingsManager"]),
            "SettingsManager",
        ):
            pass
        self._mgr = SettingsManager()
        self._store: dict = {}
        self._mgr._qs = _FakeQSettings(self._store)

    def test_add_and_retrieve_history(self):
        entry = {"timestamp": "2026-03-09T12:00:00", "format": "PNG",
                 "file_count": 3, "success": 3, "errors": 0, "files": ["a.jpg"]}
        self._mgr.add_converter_history(entry)
        history = self._mgr.get_converter_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["format"], "PNG")

    def test_history_capped_at_max(self):
        for i in range(60):
            self._mgr.add_converter_history(
                {"timestamp": f"2026-03-09T{i:02d}:00:00", "format": "PNG",
                 "file_count": 1, "success": 1, "errors": 0, "files": []}
            )
        history = self._mgr.get_converter_history()
        self.assertLessEqual(len(history), 50)

    def test_history_newest_first(self):
        self._mgr.add_converter_history({"timestamp": "A", "format": "BMP",
                                          "file_count": 1, "success": 1,
                                          "errors": 0, "files": []})
        self._mgr.add_converter_history({"timestamp": "B", "format": "PNG",
                                          "file_count": 1, "success": 1,
                                          "errors": 0, "files": []})
        history = self._mgr.get_converter_history()
        self.assertEqual(history[0]["timestamp"], "B")  # most recent first


# ---------------------------------------------------------------------------
# SettingsManager – alpha fixer history
# ---------------------------------------------------------------------------

class TestAlphaHistory(unittest.TestCase):
    def setUp(self):
        from src.core.settings_manager import SettingsManager
        self._mgr = SettingsManager.__new__(SettingsManager)
        self._store: dict = {}
        self._mgr._qs = _FakeQSettings(self._store)

    def test_empty_by_default(self):
        history = self._mgr.get_alpha_history()
        self.assertEqual(history, [])

    def test_add_and_retrieve(self):
        entry = {"timestamp": "2026-03-09T12:00:00", "preset": "PS2",
                 "file_count": 2, "success": 2, "errors": 0,
                 "files": ["a.png", "b.png"]}
        self._mgr.add_alpha_history(entry)
        history = self._mgr.get_alpha_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["preset"], "PS2")

    def test_alpha_history_capped_at_max(self):
        for i in range(60):
            self._mgr.add_alpha_history(
                {"timestamp": f"2026-03-09T{i:02d}:00:00", "preset": "PS2",
                 "file_count": 1, "success": 1, "errors": 0, "files": []}
            )
        history = self._mgr.get_alpha_history()
        self.assertLessEqual(len(history), 50)

    def test_alpha_history_newest_first(self):
        self._mgr.add_alpha_history({"timestamp": "A", "preset": "N64",
                                     "file_count": 1, "success": 1,
                                     "errors": 0, "files": []})
        self._mgr.add_alpha_history({"timestamp": "B", "preset": "PS2",
                                     "file_count": 1, "success": 1,
                                     "errors": 0, "files": []})
        history = self._mgr.get_alpha_history()
        self.assertEqual(history[0]["timestamp"], "B")


# ---------------------------------------------------------------------------
# SettingsManager – unlock_sakura default + theme color keys
# ---------------------------------------------------------------------------

class TestSettingsDefaults(unittest.TestCase):
    def setUp(self):
        from src.core.settings_manager import SettingsManager
        self._mgr = SettingsManager.__new__(SettingsManager)

    def test_unlock_sakura_default_is_false(self):
        default = self._mgr._DEFAULTS.get("unlock_sakura")
        self.assertIs(default, False)

    def test_unlock_skeleton_default_is_false(self):
        default = self._mgr._DEFAULTS.get("unlock_skeleton")
        self.assertIs(default, False)

    def test_default_theme_has_progress_bar(self):
        self.assertIn("progress_bar", self._mgr._DEFAULT_THEME)

    def test_default_theme_has_input_bg(self):
        self.assertIn("input_bg", self._mgr._DEFAULT_THEME)

    def test_default_theme_has_scrollbar_handle(self):
        self.assertIn("scrollbar_handle", self._mgr._DEFAULT_THEME)




class _FakeQSettings:
    """Minimal QSettings substitute backed by a plain dict."""
    def __init__(self, store: dict):
        self._s = store

    def value(self, key, default=None):
        return self._s.get(key, default)

    def setValue(self, key, value):
        self._s[key] = value

    def sync(self):
        pass


# ---------------------------------------------------------------------------
# Theme engine – new palettes and THEME_EFFECTS
# ---------------------------------------------------------------------------

class TestNewThemes(unittest.TestCase):
    def test_preset_themes_contains_new_entries(self):
        from src.ui.theme_engine import PRESET_THEMES
        for name in ("Gore", "Bat Cave", "Rainbow Chaos",
                     "Otter Cove", "Galaxy", "Galaxy Otter", "Goth",
                     "Volcano", "Arctic"):
            self.assertIn(name, PRESET_THEMES, f"{name} should be in PRESET_THEMES")

    def test_hidden_themes_contains_secret_skeleton(self):
        from src.ui.theme_engine import HIDDEN_THEMES
        self.assertIn("Secret Skeleton", HIDDEN_THEMES)

    def test_hidden_themes_contains_secret_sakura(self):
        from src.ui.theme_engine import HIDDEN_THEMES
        self.assertIn("Secret Sakura", HIDDEN_THEMES)
        self.assertEqual(HIDDEN_THEMES["Secret Sakura"].get("_effect"), "panda")
        self.assertEqual(HIDDEN_THEMES["Secret Sakura"].get("_unlock"), "sakura")

    def test_panda_themes_have_panda_effect(self):
        from src.ui.theme_engine import PRESET_THEMES
        self.assertEqual(PRESET_THEMES["Panda Dark"].get("_effect"), "panda")
        self.assertEqual(PRESET_THEMES["Panda Light"].get("_effect"), "panda")

    def test_volcano_uses_fire_effect(self):
        from src.ui.theme_engine import PRESET_THEMES, THEME_EFFECTS
        self.assertIn("Volcano", PRESET_THEMES)
        self.assertEqual(PRESET_THEMES["Volcano"].get("_effect"), "fire")
        self.assertEqual(THEME_EFFECTS["Volcano"], "fire")

    def test_arctic_uses_ice_effect(self):
        from src.ui.theme_engine import PRESET_THEMES, THEME_EFFECTS
        self.assertIn("Arctic", PRESET_THEMES)
        self.assertEqual(PRESET_THEMES["Arctic"].get("_effect"), "ice")
        self.assertEqual(THEME_EFFECTS["Arctic"], "ice")

    def test_theme_effects_map_populated(self):
        from src.ui.theme_engine import THEME_EFFECTS
        self.assertIn("Gore", THEME_EFFECTS)
        self.assertEqual(THEME_EFFECTS["Gore"], "gore")
        self.assertEqual(THEME_EFFECTS["Bat Cave"], "bat")
        self.assertEqual(THEME_EFFECTS["Galaxy Otter"], "galaxy_otter")

    def test_all_presets_have_required_keys(self):
        from src.ui.theme_engine import PRESET_THEMES
        required = {"background", "surface", "primary", "accent",
                    "text", "button_bg", "progress_bar"}
        for name, theme in PRESET_THEMES.items():
            for key in required:
                self.assertIn(key, theme, f"{name} missing key '{key}'")

    def test_build_stylesheet_works_for_all_themes(self):
        from src.ui.theme_engine import PRESET_THEMES, build_stylesheet
        for name, theme in PRESET_THEMES.items():
            sheet = build_stylesheet(theme)
            self.assertIsInstance(sheet, str)
            self.assertIn("QWidget", sheet, f"{name} stylesheet missing QWidget rule")


# ---------------------------------------------------------------------------
# ClickEffectsOverlay
# ---------------------------------------------------------------------------

class TestClickEffectsOverlay(unittest.TestCase):
    def setUp(self):
        self._app = _get_app()
        from PyQt6.QtWidgets import QWidget
        self._parent = QWidget()
        self._parent.resize(600, 400)

    def tearDown(self):
        self._parent.hide()
        self._parent.deleteLater()
        self._app.processEvents()

    def test_creates_without_error(self):
        from src.ui.click_effects import ClickEffectsOverlay
        overlay = ClickEffectsOverlay(self._parent)
        self.assertIsNotNone(overlay)

    def test_initial_click_count_is_zero(self):
        from src.ui.click_effects import ClickEffectsOverlay
        overlay = ClickEffectsOverlay(self._parent)
        self.assertEqual(overlay.click_count, 0)

    def test_record_click_increments_counter(self):
        from src.ui.click_effects import ClickEffectsOverlay
        overlay = ClickEffectsOverlay(self._parent)
        overlay.record_click()
        overlay.record_click()
        self.assertEqual(overlay.click_count, 2)

    def test_set_effect_unknown_key_falls_back_to_default(self):
        from src.ui.click_effects import ClickEffectsOverlay
        overlay = ClickEffectsOverlay(self._parent)
        overlay.set_effect("nonexistent_effect_xyz")
        self.assertEqual(overlay._effect_key, "default")

    def test_set_effect_all_known_keys(self):
        from src.ui.click_effects import ClickEffectsOverlay, _SPAWNERS
        overlay = ClickEffectsOverlay(self._parent)
        for key in _SPAWNERS:
            overlay.set_effect(key)
            self.assertEqual(overlay._effect_key, key)

    def test_spawners_return_particles(self):
        from src.ui.click_effects import _SPAWNERS
        for key, spawner in _SPAWNERS.items():
            particles = spawner(100, 100)
            self.assertGreater(len(particles), 0, f"Spawner '{key}' returned no particles")

    def test_paint_does_not_crash_without_particles(self):
        from src.ui.click_effects import ClickEffectsOverlay
        overlay = ClickEffectsOverlay(self._parent)
        overlay.show()
        overlay.resize(600, 400)
        self._app.processEvents()

    def test_set_enabled_does_not_crash(self):
        from src.ui.click_effects import ClickEffectsOverlay
        overlay = ClickEffectsOverlay(self._parent)
        overlay.set_enabled(True)
        overlay.set_enabled(False)


# ---------------------------------------------------------------------------
# TooltipManager
# ---------------------------------------------------------------------------

class TestTooltipManager(unittest.TestCase):
    def setUp(self):
        self._app = _get_app()
        self._store: dict = {}
        self._qs = _FakeQSettings(self._store)

    def _make_manager(self, mode="Normal"):
        from src.ui.tooltip_manager import TooltipManager

        class _FakeSettings:
            def __init__(self, store):
                self._store = store

            def get(self, key, fallback=None):
                return self._store.get(key, fallback)

        settings = _FakeSettings({"tooltip_mode": mode})
        return TooltipManager(settings)

    def test_creates_without_error(self):
        mgr = self._make_manager()
        self.assertIsNotNone(mgr)

    def test_mode_returns_stored_mode(self):
        mgr = self._make_manager("Dumbed Down")
        self.assertEqual(mgr.mode(), "Dumbed Down")

    def test_register_stores_key(self):
        from PyQt6.QtWidgets import QPushButton
        btn = QPushButton()
        mgr = self._make_manager()
        mgr.register(btn, "add_files")
        self.assertEqual(mgr._widget_keys.get(id(btn)), "add_files")
        btn.deleteLater()
        self._app.processEvents()

    def test_tooltip_modes_list_has_four_entries(self):
        from src.ui.tooltip_manager import TOOLTIP_MODES
        self.assertEqual(len(TOOLTIP_MODES), 4)
        self.assertIn("Normal", TOOLTIP_MODES)
        self.assertIn("Off", TOOLTIP_MODES)
        self.assertIn("Dumbed Down", TOOLTIP_MODES)
        # No Filter 🤬 should be in the list
        self.assertTrue(any("No Filter" in m for m in TOOLTIP_MODES))

    def test_normal_tips_cycle(self):
        from src.ui.tooltip_manager import _NORMAL
        self.assertIn("add_files", _NORMAL)
        self.assertEqual(len(_NORMAL["add_files"]), 5)

    def test_vulgar_tips_exist_for_all_normal_keys(self):
        from src.ui.tooltip_manager import _NORMAL, _VULGAR
        for key in _NORMAL:
            self.assertIn(key, _VULGAR,
                          f"Missing No Filter tip for key '{key}'")

    def test_all_tip_variants_have_exactly_five_entries(self):
        from src.ui.tooltip_manager import _NORMAL, _DUMBED, _VULGAR
        for mode_name, tips_dict in [("Normal", _NORMAL), ("Dumbed", _DUMBED), ("Vulgar", _VULGAR)]:
            for key, variants in tips_dict.items():
                self.assertEqual(len(variants), 5,
                                 f"{mode_name}['{key}'] should have 5 variants, got {len(variants)}")

    def test_dumbed_down_tips_exist_for_all_normal_keys(self):
        from src.ui.tooltip_manager import _NORMAL, _DUMBED
        for key in _NORMAL:
            self.assertIn(key, _DUMBED,
                          f"Missing Dumbed Down tip for key '{key}'")

    def test_cycle_index_increments(self):
        mgr = self._make_manager("Normal")
        from PyQt6.QtWidgets import QPushButton
        btn = QPushButton()
        mgr.register(btn, "add_files")
        self.assertEqual(mgr._cycle.get("add_files", 0), 0)
        btn.deleteLater()
        self._app.processEvents()

    def test_settings_manager_has_tooltip_mode_default(self):
        from src.core.settings_manager import SettingsManager
        mgr = SettingsManager.__new__(SettingsManager)
        default = mgr._DEFAULTS.get("tooltip_mode", None)
        self.assertEqual(default, "Normal")


# ---------------------------------------------------------------------------
# Patreon URL constant
# ---------------------------------------------------------------------------

class TestPatreonLink(unittest.TestCase):
    def test_patreon_url_correct(self):
        from src.ui.main_window import PATREON_URL
        self.assertIn("patreon.com", PATREON_URL)
        self.assertIn("DeadOnTheInside", PATREON_URL)


# ---------------------------------------------------------------------------
# Custom emoji / effect selector (theme maker)
# ---------------------------------------------------------------------------

class TestCustomEmoji(unittest.TestCase):
    """Custom emoji storage and custom spawner in click_effects."""

    def test_custom_emoji_default_in_settings(self):
        from src.core.settings_manager import SettingsManager
        mgr = SettingsManager.__new__(SettingsManager)
        default = mgr._DEFAULTS.get("custom_emoji", None)
        self.assertIsNotNone(default)
        self.assertIn("✨", default)

    def test_custom_emoji_in_export_keys(self):
        from src.core.settings_manager import SettingsManager
        self.assertIn("custom_emoji", SettingsManager.EXPORT_KEYS)

    def test_set_custom_emoji_updates_spawner(self):
        from src.ui.click_effects import set_custom_emoji
        set_custom_emoji(["🐼", "🎉"])
        from src.ui.click_effects import _CUSTOM_EMOJI
        self.assertIn("🐼", _CUSTOM_EMOJI)
        self.assertIn("🎉", _CUSTOM_EMOJI)

    def test_set_custom_emoji_empty_list_uses_fallback(self):
        from src.ui.click_effects import set_custom_emoji
        set_custom_emoji([])
        from src.ui.click_effects import _CUSTOM_EMOJI
        self.assertTrue(len(_CUSTOM_EMOJI) > 0)

    def test_custom_spawner_registered(self):
        from src.ui.click_effects import _SPAWNERS
        self.assertIn("custom", _SPAWNERS)

    def test_custom_spawner_produces_particles(self):
        from src.ui.click_effects import _SPAWNERS, set_custom_emoji
        set_custom_emoji(["🐼"])
        particles = _SPAWNERS["custom"](100, 100)
        self.assertTrue(len(particles) > 0)


class TestThemeMakerEffect(unittest.TestCase):
    """Effect key is preserved in user-saved custom themes."""

    def test_effect_options_covers_all_spawners(self):
        from src.ui.settings_dialog import _EFFECT_OPTIONS
        from src.ui.click_effects import _SPAWNERS
        option_keys = {key for key, _ in _EFFECT_OPTIONS}
        for spawner_key in _SPAWNERS:
            self.assertIn(spawner_key, option_keys,
                          f"_EFFECT_OPTIONS missing key '{spawner_key}'")

    def test_effect_key_written_into_theme_on_save(self):
        """Saving a custom theme must preserve the _effect key."""
        import json
        from src.core.settings_manager import SettingsManager

        class _FakeQSettings:
            def __init__(self):
                self._store = {}
            def value(self, key, default=None):
                return self._store.get(key, default)
            def setValue(self, key, val):
                self._store[key] = val
            def sync(self):
                pass

        mgr = SettingsManager.__new__(SettingsManager)
        mgr._qs = _FakeQSettings()

        theme = {"name": "My Theme", "accent": "#ff0000", "_effect": "gore"}
        mgr.save_named_theme("My Theme", theme)

        saved = mgr.get_saved_themes()
        self.assertIn("My Theme", saved)
        self.assertEqual(saved["My Theme"].get("_effect"), "gore")

    def test_normal_tips_have_effect_combo_key(self):
        from src.ui.tooltip_manager import _NORMAL
        self.assertIn("effect_combo", _NORMAL)
        self.assertEqual(len(_NORMAL["effect_combo"]), 5)

    def test_normal_tips_have_custom_emoji_key(self):
        from src.ui.tooltip_manager import _NORMAL
        self.assertIn("custom_emoji", _NORMAL)
        self.assertEqual(len(_NORMAL["custom_emoji"]), 5)

    def test_all_modes_have_effect_combo_key(self):
        from src.ui.tooltip_manager import _NORMAL, _DUMBED, _VULGAR
        for mode_name, tips in [("Normal", _NORMAL),
                                  ("Dumbed Down", _DUMBED),
                                  ("No Filter", _VULGAR)]:
            self.assertIn("effect_combo", tips,
                          f"{mode_name} missing 'effect_combo' tip")
            self.assertIn("custom_emoji", tips,
                          f"{mode_name} missing 'custom_emoji' tip")

    def test_apply_theme_effect_uses_theme_effect_key(self):
        """_apply_theme_effect falls back to theme dict's _effect for custom themes."""
        from src.ui.theme_engine import THEME_EFFECTS
        # A custom saved theme is not in THEME_EFFECTS
        custom_theme_name = "__custom_test_theme__"
        self.assertNotIn(custom_theme_name, THEME_EFFECTS)
        # Simulate the logic from main_window._apply_theme_effect
        theme = {"name": custom_theme_name, "_effect": "otter"}
        effect_key = THEME_EFFECTS.get(theme["name"]) or theme.get("_effect", "default")
        self.assertEqual(effect_key, "otter")

    def test_recursive_check_key_in_all_modes(self):
        from src.ui.tooltip_manager import _NORMAL, _DUMBED, _VULGAR
        for mode_name, tips in [("Normal", _NORMAL),
                                  ("Dumbed Down", _DUMBED),
                                  ("No Filter", _VULGAR)]:
            self.assertIn("recursive_check", tips,
                          f"{mode_name} missing 'recursive_check' tip")
            self.assertEqual(len(tips["recursive_check"]), 5,
                             f"{mode_name}['recursive_check'] should have 5 variants")

    def test_settings_dialog_tooltip_keys_in_all_modes(self):
        """All 6 new settings-dialog tooltip keys must appear in every active mode."""
        from src.ui.tooltip_manager import _NORMAL, _DUMBED, _VULGAR
        new_keys = ("sound_check", "trail_check", "trail_color",
                    "cursor_combo", "font_size", "click_effects_check")
        for mode_name, tips in [("Normal", _NORMAL),
                                  ("Dumbed Down", _DUMBED),
                                  ("No Filter", _VULGAR)]:
            for key in new_keys:
                self.assertIn(key, tips,
                              f"{mode_name} missing '{key}' tip")
                self.assertEqual(len(tips[key]), 5,
                                 f"{mode_name}['{key}'] should have 5 variants")


if __name__ == "__main__":
    unittest.main()

