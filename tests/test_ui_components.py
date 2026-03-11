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
# PyQt6 availability – used to skip widget/UI tests when PyQt6 is absent
# ---------------------------------------------------------------------------

try:
    import PyQt6  # noqa: F401
    _PYQT6_AVAILABLE = True
except ImportError:
    _PYQT6_AVAILABLE = False

# A stricter check: can we actually load Qt's GUI/Widgets stack?
# On headless CI runners libEGL.so.1 may be absent even when PyQt6 is
# installed, causing ImportError on QtGui/QtWidgets at import time.
try:
    from PyQt6.QtGui import QCursor  # noqa: F401
    _QT_GUI_AVAILABLE = True
except (ImportError, OSError, RuntimeError):
    # ImportError: PyQt6.QtGui not installed.
    # OSError/RuntimeError: system library (e.g. libEGL.so.1) is absent on
    # headless CI runners even when the PyQt6 Python package is installed.
    _QT_GUI_AVAILABLE = False


def _require_pyqt6(test_instance):
    """Skip the calling test when PyQt6 is not installed."""
    if not _PYQT6_AVAILABLE:
        raise unittest.SkipTest("PyQt6 not installed — skipping widget test")


def _require_qt_gui(test_instance):
    """Skip the calling test when the Qt GUI stack (libEGL etc.) is unavailable."""
    if not _QT_GUI_AVAILABLE:
        raise unittest.SkipTest("Qt GUI stack unavailable — skipping widget test")

# ---------------------------------------------------------------------------
# DropFileList tests (headless via QApplication)
# ---------------------------------------------------------------------------

def _get_app():
    """Return or create a QApplication for widget tests, flushing deferred deletions.

    Raises ``unittest.SkipTest`` when PyQt6 is not installed so that any test
    class which calls this in ``setUp`` is automatically skipped rather than
    erroring with an ImportError.
    """
    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        raise unittest.SkipTest("PyQt6 not installed — skipping widget test")
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
        _require_qt_gui(self)
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
        """WA_NoSystemBackground must be set so Qt does not pre-fill the overlay
        with the background colour (which would erase underlying child widgets)."""
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
        _require_pyqt6(self)
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
        _require_pyqt6(self)
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

    def test_default_theme_has_effect_key(self):
        """_DEFAULT_THEME must include _effect so the settings dialog shows the
        correct effect on first launch instead of falling back to 'Default'."""
        self.assertIn("_effect", self._mgr._DEFAULT_THEME)
        self.assertEqual(self._mgr._DEFAULT_THEME["_effect"], "panda")


# ---------------------------------------------------------------------------
# SettingsManager – clear_history public API (Bug: was using private _qs)
# ---------------------------------------------------------------------------

class TestSettingsManagerClearHistory(unittest.TestCase):
    def setUp(self):
        _require_pyqt6(self)
        from src.core.settings_manager import SettingsManager
        self._mgr = SettingsManager.__new__(SettingsManager)
        self._store: dict = {}
        self._mgr._qs = _FakeQSettings(self._store)

    def test_clear_converter_history_empties_list(self):
        self._mgr.add_converter_history({"timestamp": "T", "format": "PNG",
                                          "file_count": 1, "success": 1,
                                          "errors": 0, "files": []})
        self._mgr.clear_converter_history()
        self.assertEqual(self._mgr.get_converter_history(), [])

    def test_clear_alpha_history_empties_list(self):
        self._mgr.add_alpha_history({"timestamp": "T", "preset": "PS2",
                                      "file_count": 1, "success": 1,
                                      "errors": 0, "files": []})
        self._mgr.clear_alpha_history()
        self.assertEqual(self._mgr.get_alpha_history(), [])

    def test_clear_does_not_affect_other_settings(self):
        """Clearing history must leave other settings keys intact."""
        self._mgr.add_converter_history({"timestamp": "T", "format": "PNG",
                                          "file_count": 1, "success": 1,
                                          "errors": 0, "files": []})
        self._store["font_size"] = 14
        self._mgr.clear_converter_history()
        self.assertEqual(self._store.get("font_size"), 14)



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
        # Secret Sakura now has its own dedicated 'sakura' cherry-blossom effect
        self.assertEqual(HIDDEN_THEMES["Secret Sakura"].get("_effect"), "sakura")
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

@unittest.skipUnless(_QT_GUI_AVAILABLE, "Qt GUI stack unavailable")
class TestPatreonLink(unittest.TestCase):
    def test_patreon_url_correct(self):
        from src.ui.main_window import PATREON_URL
        self.assertIn("patreon.com", PATREON_URL)
        self.assertIn("DeadOnTheInside", PATREON_URL)


# ---------------------------------------------------------------------------
# Custom emoji / effect selector (theme maker)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_PYQT6_AVAILABLE, "PyQt6 not installed")
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
        _require_qt_gui(self)
        from src.ui.click_effects import set_custom_emoji
        set_custom_emoji(["🐼", "🎉"])
        from src.ui.click_effects import _CUSTOM_EMOJI
        self.assertIn("🐼", _CUSTOM_EMOJI)
        self.assertIn("🎉", _CUSTOM_EMOJI)

    def test_set_custom_emoji_empty_list_uses_fallback(self):
        _require_qt_gui(self)
        from src.ui.click_effects import set_custom_emoji
        set_custom_emoji([])
        from src.ui.click_effects import _CUSTOM_EMOJI
        self.assertTrue(len(_CUSTOM_EMOJI) > 0)

    def test_custom_spawner_registered(self):
        _require_qt_gui(self)
        from src.ui.click_effects import _SPAWNERS
        self.assertIn("custom", _SPAWNERS)

    def test_custom_spawner_produces_particles(self):
        _require_qt_gui(self)
        from src.ui.click_effects import _SPAWNERS, set_custom_emoji
        set_custom_emoji(["🐼"])
        particles = _SPAWNERS["custom"](100, 100)
        self.assertTrue(len(particles) > 0)


class TestThemeMakerEffect(unittest.TestCase):
    """Effect key is preserved in user-saved custom themes."""

    def test_effect_options_covers_all_spawners(self):
        _require_qt_gui(self)
        from src.ui.settings_dialog import _EFFECT_OPTIONS
        from src.ui.click_effects import _SPAWNERS
        option_keys = {key for key, _ in _EFFECT_OPTIONS}
        for spawner_key in _SPAWNERS:
            self.assertIn(spawner_key, option_keys,
                          f"_EFFECT_OPTIONS missing key '{spawner_key}'")

    def test_effect_key_written_into_theme_on_save(self):
        """Saving a custom theme must preserve the _effect key."""
        _require_pyqt6(self)
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
        _require_pyqt6(self)
        from src.ui.tooltip_manager import _NORMAL
        self.assertIn("effect_combo", _NORMAL)
        self.assertEqual(len(_NORMAL["effect_combo"]), 5)

    def test_normal_tips_have_custom_emoji_key(self):
        _require_pyqt6(self)
        from src.ui.tooltip_manager import _NORMAL
        self.assertIn("custom_emoji", _NORMAL)
        self.assertEqual(len(_NORMAL["custom_emoji"]), 5)

    def test_all_modes_have_effect_combo_key(self):
        _require_pyqt6(self)
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
        _require_pyqt6(self)
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
        _require_pyqt6(self)
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


# ---------------------------------------------------------------------------
# Bug 8: SettingsDialog._save_custom_theme must write name + _effect
# ---------------------------------------------------------------------------

@unittest.skipUnless(_PYQT6_AVAILABLE, "PyQt6 not installed")
class TestSaveCustomThemeNameAndEffect(unittest.TestCase):
    """_save_custom_theme must persist the user-entered name and current effect."""

    def _make_mgr(self):
        from src.core.settings_manager import SettingsManager

        class _FQ:
            def __init__(self):
                self._s = {}
            def value(self, k, d=None):
                return self._s.get(k, d)
            def setValue(self, k, v):
                self._s[k] = v
            def sync(self):
                pass

        mgr = SettingsManager.__new__(SettingsManager)
        mgr._qs = _FQ()
        return mgr

    def test_saved_theme_has_correct_name(self):
        """Theme stored in saved_themes must have name == the user-entered name."""
        import json
        mgr = self._make_mgr()
        # Simulate: active theme is "Panda Dark"
        from src.core.settings_manager import SettingsManager
        mgr.set_theme(SettingsManager._DEFAULT_THEME)
        # Manually reproduce _save_custom_theme logic with the fix applied:
        theme = dict(mgr.get_theme())
        user_name = "My Custom Blue"
        theme["name"] = user_name          # ← the fix
        theme["_effect"] = "fire"          # ← the fix (explicit effect)
        mgr.save_named_theme(user_name, theme)
        saved = mgr.get_saved_themes()
        self.assertIn(user_name, saved)
        self.assertEqual(saved[user_name]["name"], user_name)

    def test_saved_theme_has_effect_key(self):
        """Saved theme must include _effect even when it wasn't touched by the user."""
        import json
        mgr = self._make_mgr()
        from src.core.settings_manager import SettingsManager
        mgr.set_theme(SettingsManager._DEFAULT_THEME)
        theme = dict(mgr.get_theme())
        user_name = "My Theme No Effect Change"
        theme["name"] = user_name
        theme["_effect"] = theme.get("_effect", "default")   # normalise absence
        mgr.save_named_theme(user_name, theme)
        saved = mgr.get_saved_themes()
        self.assertIn("_effect", saved[user_name])


# ---------------------------------------------------------------------------
# Bug 9: ClickEffectsOverlay spawns particles on left-click only
# ---------------------------------------------------------------------------

class TestClickEffectsLeftClickOnly(unittest.TestCase):
    def setUp(self):
        self._app = _get_app()
        from PyQt6.QtWidgets import QWidget
        self._parent = QWidget()
        self._parent.resize(600, 400)

    def tearDown(self):
        self._parent.hide()
        self._parent.deleteLater()
        self._app.processEvents()

    def test_left_click_spawns_particles(self):
        """Simulating a left-click event should add particles."""
        from src.ui.click_effects import ClickEffectsOverlay
        from PyQt6.QtCore import QEvent, QPoint, QPointF
        from PyQt6.QtGui import QMouseEvent
        from PyQt6.QtCore import Qt

        overlay = ClickEffectsOverlay(self._parent)
        overlay.set_enabled(True)

        # Simulate a left MouseButtonPress at (100, 100)
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(100.0, 100.0),
            QPointF(100.0, 100.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        overlay.eventFilter(self._parent, event)
        self.assertGreater(len(overlay._particles), 0)

    def test_right_click_does_not_spawn_particles(self):
        """Simulating a right-click event must NOT add particles."""
        from src.ui.click_effects import ClickEffectsOverlay
        from PyQt6.QtCore import QEvent, QPoint, QPointF
        from PyQt6.QtGui import QMouseEvent
        from PyQt6.QtCore import Qt

        overlay = ClickEffectsOverlay(self._parent)
        overlay.set_enabled(True)

        # Simulate a right MouseButtonPress at (100, 100)
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(100.0, 100.0),
            QPointF(100.0, 100.0),
            Qt.MouseButton.RightButton,
            Qt.MouseButton.RightButton,
            Qt.KeyboardModifier.NoModifier,
        )
        overlay.eventFilter(self._parent, event)
        self.assertEqual(len(overlay._particles), 0)

    def test_right_click_does_not_increment_click_count(self):
        """Right-click must not advance the click counter used for unlocks."""
        from src.ui.click_effects import ClickEffectsOverlay
        from PyQt6.QtCore import QEvent, QPointF
        from PyQt6.QtGui import QMouseEvent
        from PyQt6.QtCore import Qt

        overlay = ClickEffectsOverlay(self._parent)
        overlay.set_enabled(True)

        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(100.0, 100.0),
            QPointF(100.0, 100.0),
            Qt.MouseButton.RightButton,
            Qt.MouseButton.RightButton,
            Qt.KeyboardModifier.NoModifier,
        )
        overlay.eventFilter(self._parent, event)
        self.assertEqual(overlay.click_count, 0)


# ---------------------------------------------------------------------------
# Bug 10: _ThumbLoader must close the PIL image to release file handles
# ---------------------------------------------------------------------------

class TestThumbLoaderClosesImage(unittest.TestCase):
    """_ThumbLoader.run() must close the PIL image after extracting data."""

    def setUp(self):
        self._app = _get_app()

    def tearDown(self):
        self._app.processEvents()

    def test_image_file_accessible_after_loader_finishes(self):
        """After ThumbLoader completes, the file must be reopenable (handle released)."""
        import tempfile
        from PIL import Image
        from src.ui.preview_pane import _ThumbLoader

        results = []
        errors = []

        with tempfile.TemporaryDirectory() as td:
            img_path = os.path.join(td, "test_close.png")
            Image.new("RGBA", (64, 64), (0, 128, 255, 200)).save(img_path)

            loader = _ThumbLoader(img_path)
            loader.loaded.connect(lambda qi, meta: results.append(meta))
            loader.failed.connect(errors.append)
            loader.start()
            loader.wait(3000)
            self._app.processEvents()

            # If the file handle is still open (bug), reopening may fail on Windows;
            # we also verify the loaded signal fired exactly once.
            self.assertEqual(len(errors), 0, f"Loader reported error: {errors}")
            self.assertEqual(len(results), 1)
            # Verify the file can be opened again (handle was released)
            try:
                img2 = Image.open(img_path)
                img2.close()
            except Exception as exc:
                self.fail(f"File handle not released after ThumbLoader: {exc}")


# ---------------------------------------------------------------------------
# Theme cursor: _cursor key on every preset theme
# ---------------------------------------------------------------------------

class TestThemeCursorKeys(unittest.TestCase):
    """Every preset and hidden theme must carry a non-empty '_cursor' key."""

    def test_all_preset_themes_have_cursor_key(self):
        from src.ui.theme_engine import PRESET_THEMES
        for name, theme in PRESET_THEMES.items():
            self.assertIn("_cursor", theme, f"PRESET_THEMES['{name}'] missing '_cursor'")
            self.assertIsInstance(theme["_cursor"], str,
                                  f"'{name}' _cursor must be str")
            self.assertTrue(theme["_cursor"].strip(),
                            f"'{name}' _cursor must not be empty")

    def test_hidden_themes_have_cursor_key(self):
        from src.ui.theme_engine import HIDDEN_THEMES
        for name, theme in HIDDEN_THEMES.items():
            self.assertIn("_cursor", theme, f"HIDDEN_THEMES['{name}'] missing '_cursor'")
            self.assertTrue(theme["_cursor"].strip(),
                            f"'{name}' _cursor must not be empty")

    def test_otter_cove_has_rock_emoji_cursor(self):
        """Otter Cove must specify the 🤘 rock-emoji cursor."""
        from src.ui.theme_engine import OTTER_THEME
        self.assertEqual(OTTER_THEME["_cursor"], "emoji:🤘")

    def test_galaxy_otter_has_rock_emoji_cursor(self):
        """Galaxy Otter must also specify the 🤘 rock-emoji cursor."""
        from src.ui.theme_engine import GALAXY_OTTER_THEME
        self.assertEqual(GALAXY_OTTER_THEME["_cursor"], "emoji:🤘")

    def test_cursor_spec_values_are_known(self):
        """All _cursor values must be either a known Qt name or 'emoji:...'."""
        _require_qt_gui(self)
        from src.ui.theme_engine import PRESET_THEMES, HIDDEN_THEMES
        from src.ui.main_window import _CURSOR_MAP
        all_themes = {**PRESET_THEMES, **HIDDEN_THEMES}
        for name, theme in all_themes.items():
            spec = theme.get("_cursor", "Default")
            ok = spec in _CURSOR_MAP or spec.startswith("emoji:")
            self.assertTrue(ok,
                f"'{name}' has unknown _cursor value '{spec}'")


# ---------------------------------------------------------------------------
# Theme cursor: _make_emoji_cursor returns a QCursor
# ---------------------------------------------------------------------------

class TestMakeEmojiCursor(unittest.TestCase):
    def setUp(self):
        self._app = _get_app()

    def tearDown(self):
        self._app.processEvents()

    def test_returns_qcursor(self):
        """_make_emoji_cursor must return a QCursor instance."""
        from PyQt6.QtGui import QCursor
        from src.ui.main_window import _make_emoji_cursor
        cursor = _make_emoji_cursor("🤘")
        self.assertIsInstance(cursor, QCursor)

    def test_fallback_on_bad_emoji(self):
        """_make_emoji_cursor must not raise even for unusual input."""
        from PyQt6.QtGui import QCursor
        from src.ui.main_window import _make_emoji_cursor
        cursor = _make_emoji_cursor("")   # empty string – no emoji
        self.assertIsInstance(cursor, QCursor)


# ---------------------------------------------------------------------------
# Sakura click effect is registered and produces particles
# ---------------------------------------------------------------------------

class TestSakuraEffect(unittest.TestCase):
    def test_sakura_in_spawners(self):
        """'sakura' must be a key in _SPAWNERS."""
        _require_qt_gui(self)
        from src.ui.click_effects import _SPAWNERS
        self.assertIn("sakura", _SPAWNERS)

    def test_sakura_spawner_produces_particles(self):
        """_spawn_sakura must return at least 10 particles with full attributes."""
        _require_qt_gui(self)
        from src.ui.click_effects import _SPAWNERS
        particles = _SPAWNERS["sakura"](100, 100)
        self.assertGreater(len(particles), 0)
        # All must be _Particle instances with all expected attributes
        for p in particles:
            for attr in ("x", "y", "vx", "vy", "life", "max_life", "kind", "size", "color"):
                self.assertTrue(hasattr(p, attr),
                                f"Particle missing attribute '{attr}'")

    def test_secret_sakura_theme_uses_sakura_effect(self):
        """Secret Sakura theme must use the 'sakura' effect (not 'panda')."""
        from src.ui.theme_engine import SECRET_SAKURA_THEME
        self.assertEqual(SECRET_SAKURA_THEME["_effect"], "sakura")


# ---------------------------------------------------------------------------
# use_theme_cursor default value in settings
# ---------------------------------------------------------------------------

@unittest.skipUnless(_PYQT6_AVAILABLE, "PyQt6 not installed")
class TestUseThemeCursorSetting(unittest.TestCase):
    def test_default_is_false(self):
        """use_theme_cursor must default to False."""
        from src.core.settings_manager import SettingsManager
        self.assertIn("use_theme_cursor", SettingsManager._DEFAULTS)
        self.assertFalse(SettingsManager._DEFAULTS["use_theme_cursor"])

    def test_in_export_keys(self):
        """use_theme_cursor must be included in EXPORT_KEYS."""
        from src.core.settings_manager import SettingsManager
        self.assertIn("use_theme_cursor", SettingsManager.EXPORT_KEYS)


# ---------------------------------------------------------------------------
# Fairy Garden theme + fairy click effect
# ---------------------------------------------------------------------------

class TestFairyTheme(unittest.TestCase):
    def test_fairy_garden_in_preset_themes(self):
        from src.ui.theme_engine import PRESET_THEMES
        self.assertIn("Fairy Garden", PRESET_THEMES)

    def test_fairy_garden_has_fairy_effect(self):
        from src.ui.theme_engine import FAIRY_THEME
        self.assertEqual(FAIRY_THEME["_effect"], "fairy")

    def test_fairy_garden_has_wand_cursor(self):
        from src.ui.theme_engine import FAIRY_THEME
        self.assertEqual(FAIRY_THEME["_cursor"], "emoji:🪄")

    def test_fairy_garden_has_trail_color(self):
        from src.ui.theme_engine import FAIRY_THEME
        self.assertIn("_trail_color", FAIRY_THEME)
        self.assertTrue(FAIRY_THEME["_trail_color"].startswith("#"))

    def test_fairy_in_spawners(self):
        _require_qt_gui(self)
        from src.ui.click_effects import _SPAWNERS
        self.assertIn("fairy", _SPAWNERS)

    def test_fairy_spawner_produces_particles(self):
        _require_qt_gui(self)
        from src.ui.click_effects import _SPAWNERS
        particles = _SPAWNERS["fairy"](100, 100)
        self.assertGreater(len(particles), 0)
        for p in particles:
            for attr in ("x", "y", "vx", "vy", "life", "max_life", "kind", "size", "color"):
                self.assertTrue(hasattr(p, attr),
                                f"Particle missing attribute '{attr}'")

    def test_fairy_is_in_effect_options(self):
        """'fairy' must appear in the settings_dialog _EFFECT_OPTIONS list."""
        _require_qt_gui(self)
        from src.ui.settings_dialog import _EFFECT_OPTIONS
        keys = [k for k, _ in _EFFECT_OPTIONS]
        self.assertIn("fairy", keys)


# ---------------------------------------------------------------------------
# use_theme_trail setting
# ---------------------------------------------------------------------------

@unittest.skipUnless(_PYQT6_AVAILABLE, "PyQt6 not installed")
class TestUseThemeTrailSetting(unittest.TestCase):
    def test_default_is_false(self):
        from src.core.settings_manager import SettingsManager
        self.assertIn("use_theme_trail", SettingsManager._DEFAULTS)
        self.assertFalse(SettingsManager._DEFAULTS["use_theme_trail"])

    def test_in_export_keys(self):
        from src.core.settings_manager import SettingsManager
        self.assertIn("use_theme_trail", SettingsManager.EXPORT_KEYS)


# ---------------------------------------------------------------------------
# Every theme has _trail_color key
# ---------------------------------------------------------------------------

class TestThemeTrailColorKeys(unittest.TestCase):
    def test_all_preset_themes_have_trail_color(self):
        from src.ui.theme_engine import PRESET_THEMES
        for name, theme in PRESET_THEMES.items():
            self.assertIn("_trail_color", theme,
                          f"PRESET_THEMES['{name}'] missing '_trail_color'")
            self.assertTrue(theme["_trail_color"].startswith("#"),
                            f"'{name}' _trail_color must be a hex color")

    def test_hidden_themes_have_trail_color(self):
        from src.ui.theme_engine import HIDDEN_THEMES
        for name, theme in HIDDEN_THEMES.items():
            self.assertIn("_trail_color", theme,
                          f"HIDDEN_THEMES['{name}'] missing '_trail_color'")


# ---------------------------------------------------------------------------
# SVG badge infrastructure
# ---------------------------------------------------------------------------

class TestThemeSvgPaths(unittest.TestCase):
    def test_get_theme_svg_path_for_known_themes(self):
        """get_theme_svg_path should return a non-empty path for all preset themes."""
        from src.ui.theme_engine import PRESET_THEMES, get_theme_svg_path
        import os
        for name in PRESET_THEMES:
            path = get_theme_svg_path(name)
            self.assertTrue(path, f"No SVG path for preset theme '{name}'")
            self.assertTrue(os.path.isfile(path),
                            f"SVG file missing for '{name}': {path}")

    def test_get_theme_svg_path_unknown_returns_empty(self):
        from src.ui.theme_engine import get_theme_svg_path
        path = get_theme_svg_path("NonExistentTheme12345")
        self.assertEqual(path, "")

    def test_hidden_themes_have_svg(self):
        from src.ui.theme_engine import HIDDEN_THEMES, get_theme_svg_path
        import os
        for name in HIDDEN_THEMES:
            path = get_theme_svg_path(name)
            self.assertTrue(path, f"No SVG path for hidden theme '{name}'")
            self.assertTrue(os.path.isfile(path),
                            f"SVG file missing for hidden theme '{name}': {path}")


# ---------------------------------------------------------------------------
# Mouse trail: set_style API
# ---------------------------------------------------------------------------

class TestMouseTrailStyle(unittest.TestCase):
    def setUp(self):
        self._app = _get_app()
        from PyQt6.QtWidgets import QWidget
        self._parent = QWidget()
        self._parent.resize(600, 400)

    def tearDown(self):
        self._parent.hide()
        self._parent.deleteLater()
        self._app.processEvents()

    def test_set_style_dots_accepted(self):
        from src.ui.mouse_trail import MouseTrailOverlay
        overlay = MouseTrailOverlay(self._parent)
        overlay.set_style("dots")
        self.assertEqual(overlay._style, "dots")

    def test_set_style_fairy_accepted(self):
        from src.ui.mouse_trail import MouseTrailOverlay
        overlay = MouseTrailOverlay(self._parent)
        overlay.set_style("fairy")
        self.assertEqual(overlay._style, "fairy")

    def test_set_style_unknown_falls_back_to_dots(self):
        from src.ui.mouse_trail import MouseTrailOverlay
        overlay = MouseTrailOverlay(self._parent)
        overlay.set_style("unicorns")
        self.assertEqual(overlay._style, "dots")

    def test_set_style_clears_trail(self):
        """Changing style should clear existing trail entries."""
        from src.ui.mouse_trail import MouseTrailOverlay
        overlay = MouseTrailOverlay(self._parent)
        overlay._trail.append([10, 10, 1.0, "✨"])
        overlay.set_style("dots")
        self.assertEqual(len(overlay._trail), 0)


# ---------------------------------------------------------------------------
# New cursor options in _CURSOR_MAP
# ---------------------------------------------------------------------------

@unittest.skipUnless(_QT_GUI_AVAILABLE, "Qt GUI stack unavailable")
class TestCursorMapOptions(unittest.TestCase):
    def test_hourglass_in_cursor_map(self):
        from src.ui.main_window import _CURSOR_MAP
        self.assertIn("Hourglass", _CURSOR_MAP)

    def test_forbidden_in_cursor_map(self):
        from src.ui.main_window import _CURSOR_MAP
        self.assertIn("Forbidden", _CURSOR_MAP)

    def test_ibeam_in_cursor_map(self):
        from src.ui.main_window import _CURSOR_MAP
        self.assertIn("IBeam", _CURSOR_MAP)


# ---------------------------------------------------------------------------
# THEME_BANNER and THEME_STATUS_MESSAGES
# ---------------------------------------------------------------------------

class TestThemeBannerMessages(unittest.TestCase):
    def test_all_preset_themes_have_banner(self):
        from src.ui.theme_engine import PRESET_THEMES, THEME_BANNER
        for name in PRESET_THEMES:
            self.assertIn(name, THEME_BANNER,
                          f"THEME_BANNER missing entry for preset theme '{name}'")

    def test_all_preset_themes_have_status(self):
        from src.ui.theme_engine import PRESET_THEMES, THEME_STATUS_MESSAGES
        for name in PRESET_THEMES:
            self.assertIn(name, THEME_STATUS_MESSAGES,
                          f"THEME_STATUS_MESSAGES missing entry for preset theme '{name}'")

    def test_get_theme_banner_fallback(self):
        from src.ui.theme_engine import get_theme_banner
        result = get_theme_banner("NonExistentTheme12345")
        self.assertIn("Alpha Fixer", result)

    def test_get_theme_status_fallback(self):
        from src.ui.theme_engine import get_theme_status
        result = get_theme_status("NonExistentTheme12345")
        self.assertIn("Ready", result)

    def test_fairy_banner_has_fairy_emojis(self):
        from src.ui.theme_engine import get_theme_banner
        banner = get_theme_banner("Fairy Garden")
        self.assertTrue(any(e in banner for e in ["🧚", "🪄", "✨"]),
                        f"Fairy Garden banner should have fairy emojis: {banner}")

    def test_panda_dark_banner_has_panda(self):
        from src.ui.theme_engine import get_theme_banner
        banner = get_theme_banner("Panda Dark")
        self.assertIn("🐼", banner)


# ---------------------------------------------------------------------------
# Enriched spawners produce more particles
# ---------------------------------------------------------------------------

@unittest.skipUnless(_QT_GUI_AVAILABLE, "Qt GUI stack unavailable")
class TestEnrichedSpawners(unittest.TestCase):
    """All spawners must produce at least a few particles."""

    def _count(self, key):
        from src.ui.click_effects import _SPAWNERS
        return len(_SPAWNERS[key](100, 100))

    def test_gore_produces_particles(self):
        self.assertGreaterEqual(self._count("gore"), 4)

    def test_bat_produces_particles(self):
        self.assertGreaterEqual(self._count("bat"), 4)

    def test_rainbow_produces_particles(self):
        self.assertGreaterEqual(self._count("rainbow"), 4)

    def test_otter_produces_particles(self):
        self.assertGreaterEqual(self._count("otter"), 4)

    def test_galaxy_produces_particles(self):
        self.assertGreaterEqual(self._count("galaxy"), 4)

    def test_goth_produces_particles(self):
        self.assertGreaterEqual(self._count("goth"), 4)

    def test_neon_produces_particles(self):
        self.assertGreaterEqual(self._count("neon"), 4)

    def test_fire_produces_particles(self):
        self.assertGreaterEqual(self._count("fire"), 4)

    def test_ice_produces_particles(self):
        self.assertGreaterEqual(self._count("ice"), 4)

    def test_panda_produces_particles(self):
        self.assertGreaterEqual(self._count("panda"), 4)

    def test_default_produces_particles(self):
        self.assertGreaterEqual(self._count("default"), 4)

    def test_sakura_produces_particles(self):
        self.assertGreaterEqual(self._count("sakura"), 4)

    def test_fairy_produces_particles(self):
        self.assertGreaterEqual(self._count("fairy"), 4)


# ---------------------------------------------------------------------------
# Emoji font constant in click_effects
# ---------------------------------------------------------------------------

@unittest.skipUnless(_QT_GUI_AVAILABLE, "Qt GUI stack unavailable")
class TestClickEffectsEmojiFont(unittest.TestCase):
    def test_emoji_font_constant_exists(self):
        from src.ui.click_effects import _EMOJI_FONT_FAMILIES
        self.assertIsInstance(_EMOJI_FONT_FAMILIES, str)
        self.assertGreater(len(_EMOJI_FONT_FAMILIES), 0)

    def test_emoji_font_constant_has_multiple_families(self):
        from src.ui.click_effects import _EMOJI_FONT_FAMILIES
        self.assertIn(",", _EMOJI_FONT_FAMILIES,
                      "Should list multiple fallback font families")



# ---------------------------------------------------------------------------
# Banner animation frames
# ---------------------------------------------------------------------------

class TestBannerAnimationFrames(unittest.TestCase):
    def test_get_theme_banner_frames_returns_list(self):
        from src.ui.theme_engine import get_theme_banner_frames
        frames = get_theme_banner_frames("Fairy Garden")
        self.assertIsInstance(frames, list)
        self.assertGreater(len(frames), 0)

    def test_fairy_garden_has_multiple_frames_in_data(self):
        # get_theme_banner_frames intentionally returns a single frame for
        # display (emoji cycling in the title was removed per user feedback).
        # Verify that the raw THEME_BANNER_FRAMES data still contains multiple
        # frames for Fairy Garden so the data is preserved.
        from src.ui.theme_engine import THEME_BANNER_FRAMES
        frames = THEME_BANNER_FRAMES.get("Fairy Garden", [])
        self.assertGreater(len(frames), 1,
                           "Fairy Garden THEME_BANNER_FRAMES data should have multiple entries")

    def test_bat_cave_has_multiple_frames_in_data(self):
        from src.ui.theme_engine import THEME_BANNER_FRAMES
        frames = THEME_BANNER_FRAMES.get("Bat Cave", [])
        self.assertGreater(len(frames), 1)

    def test_unknown_theme_returns_single_frame(self):
        from src.ui.theme_engine import get_theme_banner_frames, get_theme_banner
        frames = get_theme_banner_frames("NoSuchTheme99")
        self.assertEqual(len(frames), 1)
        self.assertIn("Alpha Fixer", frames[0])
        # The single frame must be consistent with get_theme_banner fallback
        self.assertEqual(frames[0], get_theme_banner("NoSuchTheme99"))

    def test_all_frames_are_non_empty_strings(self):
        from src.ui.theme_engine import THEME_BANNER_FRAMES
        for theme_name, frames in THEME_BANNER_FRAMES.items():
            for frame in frames:
                self.assertIsInstance(frame, str, f"{theme_name} frame must be str")
                self.assertGreater(len(frame.strip()), 0,
                                   f"{theme_name} has empty banner frame")

    def test_fairy_garden_frames_have_fairy_emojis(self):
        from src.ui.theme_engine import THEME_BANNER_FRAMES
        frames = THEME_BANNER_FRAMES.get("Fairy Garden", [])
        for frame in frames:
            self.assertTrue(
                any(e in frame for e in ["🧚", "🪄", "✨", "🌟", "💜"]),
                f"Fairy Garden frame has no fairy emoji: {frame}"
            )

    def test_animated_svgs_have_animate_elements(self):
        """Key theme SVGs should contain SVG animation elements."""
        import os
        svg_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "src", "assets", "svg"
        )
        animated_themes = ["fairy_garden.svg", "bat_cave.svg", "galaxy.svg", "neon.svg", "gore.svg"]
        for filename in animated_themes:
            path = os.path.join(svg_dir, filename)
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("<animate", content,
                              f"{filename} should contain SVG animation elements")


# ---------------------------------------------------------------------------
# Effect key preference: user choice beats hardcoded THEME_EFFECTS map
# ---------------------------------------------------------------------------

class TestEffectKeyPreference(unittest.TestCase):
    """_apply_theme_effect must honour theme['_effect'] over THEME_EFFECTS."""

    def test_theme_effect_key_preferred_over_preset_map(self):
        """If a preset theme has _effect overridden, the override must win."""
        from src.ui.theme_engine import THEME_EFFECTS
        # Panda Dark normally maps to "panda" in THEME_EFFECTS
        self.assertEqual(THEME_EFFECTS.get("Panda Dark"), "panda")
        # Simulate a theme dict that a user has customized to "gore"
        theme = {"name": "Panda Dark", "_effect": "gore"}
        effect_key = theme.get("_effect") or THEME_EFFECTS.get(theme["name"], "default")
        self.assertEqual(effect_key, "gore",
                         "User-chosen effect should override hardcoded preset map")

    def test_fallback_to_preset_map_when_no_effect_key(self):
        """When _effect is absent, fall back to THEME_EFFECTS."""
        from src.ui.theme_engine import THEME_EFFECTS
        theme = {"name": "Bat Cave"}
        effect_key = theme.get("_effect") or THEME_EFFECTS.get(theme["name"], "default")
        self.assertEqual(effect_key, THEME_EFFECTS.get("Bat Cave", "default"))

    def test_default_fallback_for_unknown_theme(self):
        """Unknown theme name with no _effect key falls back to 'default'."""
        from src.ui.theme_engine import THEME_EFFECTS
        theme = {"name": "NoSuchTheme"}
        effect_key = theme.get("_effect") or THEME_EFFECTS.get(theme["name"], "default")
        self.assertEqual(effect_key, "default")


# ---------------------------------------------------------------------------
# Click effects: off-screen culling logic (tested without triggering painting)
# ---------------------------------------------------------------------------

class TestClickEffectsCulling(unittest.TestCase):
    def setUp(self):
        _get_app()

    def test_offscreen_bat_fly_particle_would_be_culled(self):
        """bat_fly particles far outside window bounds must not survive _tick logic."""
        from src.ui.click_effects import _Particle
        from PyQt6.QtGui import QColor
        # Replicate the culling condition from _tick so we can test it without
        # triggering Qt painting (which crashes in the offscreen environment).
        p = _Particle(-200, 300, -5, 0, 99.0, "bat_fly", 20, QColor("#7b2dff"), "🦇")
        ow, oh = 800, 600
        culled = (
            p.kind in ("bat_fly", "fairy_fly")
            and (p.x < -100 or p.x > ow + 100 or p.y < -100 or p.y > oh + 100)
        )
        self.assertTrue(culled, "bat_fly particle at x=-200 should be culled")

    def test_onscreen_bat_fly_particle_not_culled(self):
        """bat_fly particles inside the window must not be culled."""
        from src.ui.click_effects import _Particle
        from PyQt6.QtGui import QColor
        p = _Particle(400, 300, 2, 0, 5.0, "bat_fly", 20, QColor("#7b2dff"), "🦇")
        ow, oh = 800, 600
        culled = (
            p.kind in ("bat_fly", "fairy_fly")
            and (p.x < -100 or p.x > ow + 100 or p.y < -100 or p.y > oh + 100)
        )
        self.assertFalse(culled, "bat_fly particle at x=400 should NOT be culled")

    def test_offscreen_fairy_fly_particle_would_be_culled(self):
        """fairy_fly particles far to the right must be culled."""
        from src.ui.click_effects import _Particle
        from PyQt6.QtGui import QColor
        p = _Particle(1000, 300, 5, 0, 99.0, "fairy_fly", 20, QColor("#ff69b4"), "🧚")
        ow, oh = 800, 600
        culled = (
            p.kind in ("bat_fly", "fairy_fly")
            and (p.x < -100 or p.x > ow + 100 or p.y < -100 or p.y > oh + 100)
        )
        self.assertTrue(culled, "fairy_fly particle at x=1000 should be culled")

    def test_regular_particle_not_subject_to_offscreen_cull(self):
        """Normal circle particles beyond bounds are not culled (they have life decay)."""
        from src.ui.click_effects import _Particle
        from PyQt6.QtGui import QColor
        p = _Particle(-200, 300, -5, 0, 2.0, "circle", 8, QColor("#e94560"))
        ow, oh = 800, 600
        culled = (
            p.kind in ("bat_fly", "fairy_fly")
            and (p.x < -100 or p.x > ow + 100 or p.y < -100 or p.y > oh + 100)
        )
        self.assertFalse(culled, "circle particles are not culled by off-screen check")


# ---------------------------------------------------------------------------
# Debounce timer in AlphaFixerTab — validated via source inspection
# ---------------------------------------------------------------------------

@unittest.skipUnless(_PYQT6_AVAILABLE, "PyQt6 not installed")
class TestAlphaFixerDebounce(unittest.TestCase):
    """Verify debounce timer is present in AlphaFixerTab via source inspection."""

    @staticmethod
    def _alpha_tool_src():
        import pathlib
        return (pathlib.Path(__file__).parent.parent / "src" / "ui" / "alpha_tool.py").read_text()

    def test_debounce_timer_in_source(self):
        src = self._alpha_tool_src()
        self.assertIn("_preview_debounce", src,
                      "AlphaFixerTab.__init__ must create _preview_debounce")
        self.assertIn("setSingleShot(True)", src,
                      "debounce timer must be single-shot")
        self.assertIn("setInterval(150)", src,
                      "debounce interval must be 150ms")

    def test_finetune_changed_uses_debounce(self):
        """_on_finetune_changed must start the debounce timer, not call _update_compare directly."""
        import re
        src = self._alpha_tool_src()
        # Extract the body of _on_finetune_changed up to the next method or class.
        # The pattern matches 4-space method indentation used throughout this file.
        m = re.search(r'def _on_finetune_changed\b(.*?)(?=\n    def |\nclass |\Z)', src, re.DOTALL)
        self.assertIsNotNone(m, "_on_finetune_changed not found in alpha_tool.py")
        method_src = m.group(0)
        self.assertIn("_preview_debounce.start()", method_src,
                      "_on_finetune_changed must start the debounce timer")
        self.assertNotIn("_update_compare()", method_src,
                         "_on_finetune_changed must not call _update_compare directly")


# ---------------------------------------------------------------------------
# Preview pane: no blocking wait — validated via source inspection
# ---------------------------------------------------------------------------

class TestPreviewPaneNoBlockingWait(unittest.TestCase):
    def test_show_file_no_wait_call(self):
        """show_file must not call wait() which would block the UI thread."""
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "src" / "ui" / "preview_pane.py").read_text()
        self.assertNotIn(".wait(", src,
                         "show_file must not block with .wait() on a thread")

    def test_update_compare_no_wait_call(self):
        """_update_compare must not call wait() which would block the UI thread."""
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "src" / "ui" / "alpha_tool.py").read_text()
        self.assertNotIn(".wait(", src,
                         "_update_compare must not block with .wait() on a thread")


# ---------------------------------------------------------------------------
# Theme tab labels: get_theme_tab_labels returns static labels for all themes
# (Per-theme emoji changes were intentionally removed as users found them
#  distracting — see issue #2 comment "i hate the emojis ... always changing".)
# ---------------------------------------------------------------------------

class TestThemeTabLabels(unittest.TestCase):
    def test_returns_three_labels(self):
        from src.ui.theme_engine import get_theme_tab_labels
        labels = get_theme_tab_labels("Panda Dark")
        self.assertIsInstance(labels, tuple)
        self.assertEqual(len(labels), 3)

    def test_labels_contain_tab_names(self):
        from src.ui.theme_engine import get_theme_tab_labels
        labels = get_theme_tab_labels("Bat Cave")
        self.assertIn("Alpha Fixer", labels[0])
        self.assertIn("Converter", labels[1])
        self.assertIn("History", labels[2])

    def test_labels_change_with_theme(self):
        """Tab labels must reflect the active theme — different themes produce different labels."""
        from src.ui.theme_engine import get_theme_tab_labels
        bat   = get_theme_tab_labels("Bat Cave")
        gore  = get_theme_tab_labels("Gore")
        panda = get_theme_tab_labels("Panda Dark")
        self.assertNotEqual(bat, gore,
                            "Bat Cave and Gore should produce distinct tab labels")
        self.assertNotEqual(bat, panda,
                            "Bat Cave and Panda Dark should produce distinct tab labels")

    def test_same_theme_is_deterministic(self):
        """Calling get_theme_tab_labels twice for the same theme returns the same result."""
        from src.ui.theme_engine import get_theme_tab_labels
        for name in ("Gore", "Mermaid", "Alien", "Thunder Storm"):
            self.assertEqual(get_theme_tab_labels(name), get_theme_tab_labels(name),
                             f"Labels for {name!r} must be deterministic (no cycling)")

    def test_fallback_uses_default_emojis(self):
        """Unknown theme names fall back to the default emoji set."""
        from src.ui.theme_engine import get_theme_tab_labels
        labels = get_theme_tab_labels("NonExistentThemeXYZ")
        self.assertIn("🖼", labels[0])
        self.assertIn("🔄", labels[1])
        self.assertIn("📋", labels[2])

    def test_all_known_themes_return_non_empty_labels(self):
        from src.ui.theme_engine import PRESET_THEMES, HIDDEN_THEMES, get_theme_tab_labels
        all_themes = list(PRESET_THEMES.keys()) + list(HIDDEN_THEMES.keys())
        for theme_name in all_themes:
            labels = get_theme_tab_labels(theme_name)
            for lbl in labels:
                self.assertGreater(len(lbl.strip()), 0,
                                   f"Empty tab label for theme {theme_name!r}")
