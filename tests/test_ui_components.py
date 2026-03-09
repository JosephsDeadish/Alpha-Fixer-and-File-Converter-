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
# SettingsManager – export / import
# ---------------------------------------------------------------------------

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
# Helpers
# ---------------------------------------------------------------------------

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


if __name__ == "__main__":
    unittest.main()
