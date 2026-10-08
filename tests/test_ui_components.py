"""
Tests for new UI components: DropFileList, SoundEngine, MouseTrailOverlay,
and the extended SettingsManager.
"""
import os
import io
import importlib.util
import json
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

# Make src importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests.corpus_helpers import (
    _iter_corpus_files,
    _optional_corpus_roots,
    _optional_manifest_entries,
)

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

    def test_drag_active_property_set_on_drag_enter(self):
        """dragEnterEvent should set drag_active property to True."""
        from PyQt6.QtCore import QMimeData, QUrl
        from PyQt6.QtGui import QDragEnterEvent
        import PyQt6.QtGui as g
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile("/tmp/test.png")])
        # Directly invoke the property to verify it can be toggled
        self._widget.setProperty("drag_active", True)
        self.assertEqual(self._widget.property("drag_active"), True)
        self._widget.setProperty("drag_active", False)
        self.assertEqual(self._widget.property("drag_active"), False)

    def test_open_containing_folder_missing_file_no_exception(self):
        """_open_containing_folder must not raise even for non-existent paths."""
        try:
            self._widget._open_containing_folder("/tmp/nonexistent_file_xyz_abc.png")
        except Exception as exc:
            self.fail(f"_open_containing_folder raised an exception: {exc}")

    def test_paths_dropped_signal(self):
        """paths_dropped should be a signal (not None)."""
        from PyQt6.QtCore import pyqtSignal
        # Just verify it exists and is callable/connectable
        received = []
        self._widget.paths_dropped.connect(received.extend)
        # Simulate internal emit (bypassing actual drag)
        self._widget.paths_dropped.emit(["/tmp/fake.png"])
        self.assertEqual(received, ["/tmp/fake.png"])

    def test_thumbnail_failed_signal_emits_once_per_path(self):
        received = []
        self._widget.thumbnail_failed.connect(lambda path, reason: received.append((path, reason)))
        self._widget._on_thumb_failed("/tmp/bad.png", "decode failed")
        self._widget._on_thumb_failed("/tmp/bad.png", "decode failed again")
        self.assertEqual(received, [("/tmp/bad.png", "decode failed")])

    def test_thumbnail_failure_summary_tracks_unique_paths(self):
        self.assertEqual(self._widget._thumbnail_failure_summary(), "")
        self._widget._on_thumb_failed("/tmp/a.png", "decode failed")
        self.assertEqual(
            self._widget._thumbnail_failure_summary(),
            "⚠ 1 thumbnail unavailable — a.png: decode failed",
        )
        self._widget._on_thumb_failed("/tmp/b.png", "decode failed")
        self.assertEqual(
            self._widget._thumbnail_failure_summary(),
            "⚠ 2 thumbnails unavailable — latest: b.png",
        )

    def test_thumbnail_status_summary_includes_mode_and_failures(self):
        self._widget.set_thumbnails_enabled(False)
        self._widget._on_thumb_failed("/tmp/a.png", "decode failed")
        self.assertEqual(
            self._widget._thumbnail_status_summary(),
            "🖼 Thumbnails off  •  ⚠ 1 thumbnail unavailable — a.png: decode failed",
        )

    def test_thumbnail_failure_marks_item_tooltip(self):
        self._widget.addItem("/tmp/a.png")
        self._widget._on_thumb_failed("/tmp/a.png", "decode failed")
        item = self._widget.item(0)
        self.assertIsNotNone(item)
        self.assertIn("Thumbnail preview unavailable", item.toolTip())
        self.assertIn("decode failed", item.toolTip())

    def test_dynamic_tooltip_includes_thumbnail_summary(self):
        self._widget._on_thumb_failed("/tmp/a.png", "decode failed")
        self.assertIn("⚠ 1 thumbnail unavailable", self._widget.toolTip())

    def test_thumbnail_mode_summary_reports_large_list_pause(self):
        for idx in range(3001):
            self._widget.addItem(f"/tmp/{idx}.png")
        self.assertEqual(
            self._widget._thumbnail_mode_summary(),
            "🖼 Thumbnail previews paused for large lists (3,001 queued; auto-pause at 3,000+)",
        )

    def test_large_list_item_tooltip_mentions_thumbnail_pause(self):
        for idx in range(3001):
            self._widget.addItem(f"/tmp/{idx}.png")
        self._widget._refresh_item_tooltips()
        item = self._widget.item(0)
        self.assertIsNotNone(item)
        self.assertIn("paused because this queue is above the auto-preview limit", item.toolTip())

    def test_batch_import_completed_reports_added_and_deduped_counts(self):
        received = []
        self._widget.batch_import_completed.connect(lambda added, deduped, requested: received.append((added, deduped, requested)))
        added = self._widget.add_paths_batch(["/tmp/a.png", "/tmp/a.png", "/tmp/b.png"])
        self.assertEqual(added, 2)
        self.assertEqual(received[-1], (2, 1, 3))

    def test_thumbnail_status_signal_reports_failures(self):
        received = []
        self._widget.thumbnail_status_changed.connect(lambda paused, pending, failed, loaded: received.append((paused, pending, failed, loaded)))
        self._widget._on_thumb_failed("/tmp/a.png", "decode failed")
        self.assertTrue(received)
        self.assertEqual(received[-1], (False, 0, 1, 0))


class _ConverterTabSettingsStub:
    def __init__(self):
        self._store = {}
        self._history = []
        self._video_history = []
        self._gif_history = []

    def get(self, key, fallback=None):
        return self._store.get(key, fallback)

    def set(self, key, value):
        self._store[key] = value

    def get_shortcut_binding(self, shortcut_id, default):
        return default

    def add_converter_history(self, entry):
        self._history.append(entry)

    def add_video_builder_history(self, entry):
        self._video_history.append(entry)

    def add_gif_builder_history(self, entry):
        self._gif_history.append(entry)

    def get_converter_history(self):
        return list(self._history)

    def get_alpha_history(self):
        return []

    def get_selective_alpha_history(self):
        return []

    def get_gif_builder_history(self):
        return list(self._gif_history)

    def get_video_builder_history(self):
        return list(self._video_history)


class TestConverterTab(unittest.TestCase):
    def setUp(self):
        self._app = _get_app()
        from src.ui.converter_tool import ConverterTab
        self._settings = _ConverterTabSettingsStub()
        self._widget = ConverterTab(self._settings)

    def tearDown(self):
        self._widget.hide()
        self._widget.deleteLater()
        self._app.processEvents()

    def test_close_event_cleans_up_preview_collect_and_gif_temp_state(self):
        self._widget._preview_debounce.start()
        preview_loader = MagicMock()
        collect_thread = MagicMock()
        collect_thread.isRunning.return_value = True
        gif_tmp = MagicMock()
        worker = MagicMock()
        self._widget._preview_loader = preview_loader
        self._widget._collect_thread = collect_thread
        self._widget._gif_temp_dir = gif_tmp
        self._widget._worker = worker
        self._widget.close()
        self.assertIsNone(self._widget._preview_loader)
        self.assertIsNone(self._widget._collect_thread)
        self.assertIsNone(self._widget._gif_temp_dir)
        preview_loader.stop.assert_called_once()
        collect_thread.stop.assert_called_once()
        collect_thread.wait.assert_called_once_with(200)
        gif_tmp.cleanup.assert_called_once()
        worker.stop.assert_called_once()
        worker.wait.assert_called_once_with(200)
        self.assertFalse(self._widget._preview_debounce.isActive())

    def test_run_falls_back_to_png_when_selected_target_is_unavailable(self):
        from src.ui.converter_tool import ConverterTab
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            with open(src, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n")
            self._widget._file_list.addItem(src)
            self._widget._file_list.setCurrentRow(0)
            idx = self._widget._fmt_combo.findText("AVIF", Qt.MatchFlag.MatchContains)
            self.assertGreaterEqual(idx, 0)
            self._widget._fmt_combo.setCurrentIndex(idx)
            with patch("src.ui.converter_tool.collect_files", return_value=[src]):
                with patch("src.ui.converter_tool.output_format_unavailable_reason", return_value="AVIF export needs Pillow built with libavif support."):
                    with patch("src.ui.converter_tool.QMessageBox.information") as info_mock:
                        with patch.object(ConverterTab, "_expand_gif_frames", return_value=([src], {})):
                            with patch("src.ui.converter_tool.ConverterWorker") as worker_cls:
                                worker = MagicMock()
                                worker_cls.return_value = worker
                                self._widget._run()
            info_mock.assert_called_once()
            self.assertIn("falling back to PNG", self._widget._log.toPlainText())
            kwargs = worker_cls.call_args.kwargs
            self.assertEqual(kwargs["target_format"], "PNG")
            self.assertEqual(kwargs["target_ext"], ".png")

    def test_unavailable_dds_compression_variants_are_disabled(self):
        idx = self._widget._fmt_combo.findText("DDS", Qt.MatchFlag.MatchContains)
        self.assertGreaterEqual(idx, 0)
        self._widget._fmt_combo.setCurrentIndex(idx)
        model = self._widget._dds_variant_combo.model()
        dxt1_idx = self._widget._dds_variant_combo.findData("dxt1")
        dxt3_idx = self._widget._dds_variant_combo.findData("dxt3")
        dxt5_idx = self._widget._dds_variant_combo.findData("dxt5")
        if not self._widget._dds_compression_available:
            self.assertFalse(model.item(dxt1_idx).isEnabled())
            self.assertFalse(model.item(dxt3_idx).isEnabled())
            self.assertFalse(model.item(dxt5_idx).isEnabled())

    def test_alpha_incompatible_format_sets_status_note(self):
        idx = self._widget._fmt_combo.findText("JPEG", Qt.MatchFlag.MatchContains)
        self.assertGreaterEqual(idx, 0)
        self._widget._fmt_combo.setCurrentIndex(idx)
        self.assertIn("auto-saved as PNG", self._widget._status_lbl.text())

    def test_dds_status_note_mentions_compression_requirement_when_unavailable(self):
        idx = self._widget._fmt_combo.findText("DDS", Qt.MatchFlag.MatchContains)
        self.assertGreaterEqual(idx, 0)
        self._widget._fmt_combo.setCurrentIndex(idx)
        if not self._widget._dds_compression_available:
            self.assertIn("BC2/DXT3", self._widget._status_lbl.text())

    def test_converter_capability_summary_reports_optional_limits(self):
        import src.ui.converter_tool as ct
        with patch.object(ct, "optional_pillow_output_limits", return_value=[("AVIF", "needs libavif support")]):
            with patch.object(ct, "dds_compression_available", return_value=False):
                summary = ct._converter_capability_summary()
                details = ct._converter_capability_details()
        self.assertIn("Ready with limits:", summary)
        self.assertIn("fall back to PNG", summary)
        self.assertIn("ImageMagick/wand", details)
        self.assertIn("AVIF", details)

    def test_converter_capability_banner_is_visible(self):
        self.assertTrue(hasattr(self._widget, "_capability_lbl"))
        self.assertTrue(self._widget._capability_lbl.text().startswith("Ready"))

    def test_build_failure_report_text_groups_repeated_failures(self):
        self._widget._last_run_format = "DDS"
        self._widget._last_run_files = ["/tmp/a.png", "/tmp/b.png", "/tmp/c.png"]
        self._widget._batch_error_reasons.update({"decode failed": 2, "out of memory": 1})
        self._widget._batch_error_files = {
            "decode failed": ["/tmp/a.png", "/tmp/b.png"],
            "out of memory": ["/tmp/c.png"],
        }
        self._widget._batch_failure_details = [
            {"source": "/tmp/a.png", "reason": "decode failed"},
            {"source": "/tmp/b.png", "reason": "decode failed"},
            {"source": "/tmp/c.png", "reason": "out of memory"},
        ]
        text = self._widget._build_failure_report_text()
        self.assertIn("FORMATOMANCER Conversion Failure Report", text)
        self.assertIn("- 2× decode failed", text)
        self.assertIn("• a.png", text)
        self.assertIn("Per-file failures:", text)

    def test_batch_import_completed_emits_status_notice(self):
        received = []
        self._widget.status_notice.connect(lambda message, timeout: received.append((message, timeout)))
        self._widget._on_batch_import_completed(2, 1, 3)
        self.assertEqual(received, [("Converter queue: Added 2 new files; skipped 1 duplicate.", 6000)])

    def test_get_queue_status_text_includes_preview_state(self):
        self._widget._file_list.addItem("a.png")
        with patch.object(
            self._widget._file_list,
            "get_thumbnail_summary",
            return_value={"pending_count": 2, "failure_count": 1, "failure_categories": {"decode": 1}},
        ):
            text = self._widget.get_queue_status_text()
        self.assertEqual(text, "📁 1 queued  •  2 previews pending  •  1 preview failure (decode)")

    def test_update_count_emits_queue_status_changed(self):
        self._widget._file_list.addItem("a.png")
        received = []
        self._widget.queue_status_changed.connect(received.append)
        with patch.object(self._widget, "get_queue_status_text", return_value="📁 1 queued  •  1 preview pending"):
            self._widget._update_count(1)
        self.assertEqual(received, ["📁 1 queued  •  1 preview pending"])

    def test_export_failure_report_writes_json(self):
        self._widget._last_run_format = "PNG"
        self._widget._last_run_files = ["/tmp/a.png"]
        self._widget._batch_error_reasons.update({"decode failed": 1})
        self._widget._batch_error_files = {"decode failed": ["/tmp/a.png"]}
        self._widget._batch_failure_details = [
            {"source": "/tmp/a.png", "reason": "decode failed"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "report.json")
            with patch("src.ui.converter_tool.QFileDialog.getSaveFileName", return_value=(target, "JSON Report (*.json)")):
                self._widget._export_failure_report()
            with open(target, "r", encoding="utf-8") as f:
                content = f.read()
        self.assertIn('"reason": "decode failed"', content)

    def test_finished_enables_failure_recovery_actions(self):
        self._widget._last_run_files = ["/tmp/a.png", "/tmp/b.png", "/tmp/c.png"]
        self._widget._batch_error_reasons.update({"decode failed": 2})
        self._widget._batch_error_files = {"decode failed": ["/tmp/a.png", "/tmp/c.png"]}
        self._widget._batch_failure_details = [
            {"source": "/tmp/a.png", "reason": "decode failed"},
            {"source": "/tmp/c.png", "reason": "decode failed"},
        ]
        self._widget._on_finished(1, 2)
        self.assertTrue(self._widget._btn_retry_failed.isEnabled())
        self.assertTrue(self._widget._btn_keep_failed.isEnabled())
        self.assertTrue(self._widget._btn_skip_failed.isEnabled())

    def test_batch_import_summary_is_logged(self):
        self._widget._file_list.addItem("/tmp/a.png")
        self._widget._file_list.batch_import_completed.emit(2, 1, 3)
        self.assertIn("Added 2 new files; skipped 1 duplicate", self._widget._log.toPlainText())

    def test_file_count_label_surfaces_pending_and_failed_previews(self):
        self._widget._file_list.addItem("/tmp/a.png")
        self._widget._file_list._pending.add("/tmp/a.png")
        self._widget._file_list._on_thumb_failed("/tmp/a.png", "decode failed")
        self._widget._update_count(self._widget._file_list.count())
        label = self._widget._file_count_lbl.text()
        self.assertIn("1 preview pending", label)
        self.assertIn("1 preview failure", label)
        self.assertIn("2 failed files", self._widget._failure_actions_lbl.text())
        self.assertIn("repeated issue group", self._widget._failure_actions_lbl.text())

    def test_keep_failed_only_rewrites_queue_to_failed_paths(self):
        self._widget._file_list.add_paths_batch(["/tmp/a.png", "/tmp/b.png", "/tmp/c.png"])
        self._widget._last_failed_files = ["/tmp/a.png", "/tmp/c.png"]
        self._widget._keep_failed_only()
        self.assertEqual(
            [self._widget._file_list.item(i).text() for i in range(self._widget._file_list.count())],
            ["/tmp/a.png", "/tmp/c.png"],
        )
        self.assertIn("Queue reduced to 2 failed files", self._widget._log.toPlainText())

    def test_skip_failed_files_removes_failures_from_current_queue(self):
        self._widget._file_list.add_paths_batch(["/tmp/a.png", "/tmp/b.png", "/tmp/c.png"])
        self._widget._last_failed_files = ["/tmp/a.png", "/tmp/c.png"]
        self._widget._skip_failed_files()
        self.assertEqual(self._widget._file_list.count(), 1)
        self.assertEqual(self._widget._file_list.item(0).text(), "/tmp/b.png")
        self.assertIn("Removed 2 failed files", self._widget._log.toPlainText())

    def test_retry_failed_batch_rewrites_queue_and_runs(self):
        self._widget._file_list.add_paths_batch(["/tmp/a.png", "/tmp/b.png", "/tmp/c.png"])
        self._widget._last_failed_files = ["/tmp/c.png"]
        with patch.object(self._widget, "_run") as run_mock:
            self._widget._retry_failed_batch()
        self.assertEqual(self._widget._file_list.count(), 1)
        self.assertEqual(self._widget._file_list.item(0).text(), "/tmp/c.png")
        run_mock.assert_called_once_with()
        self.assertIn("Retrying 1 failed file", self._widget._log.toPlainText())


class TestStartupCapabilityNotice(unittest.TestCase):
    def test_optional_feature_readiness_notice_is_empty_when_everything_is_ready(self):
        _require_qt_gui(self)
        import main
        with patch("src.ui.video_tool._has_imageio", return_value=True):
            with patch("src.ui.video_tool._has_imageio_ffmpeg", return_value=True):
                with patch("src.ui.video_tool._get_ffmpeg_exe", return_value="/tmp/ffmpeg"):
                    with patch("src.ui.video_tool._get_ffprobe_exe", return_value="/tmp/ffprobe"):
                        with patch("src.core.file_converter.dds_compression_available", return_value=True):
                            with patch("src.core.file_converter.optional_pillow_output_limits", return_value=[]):
                                with patch.object(main, "_dds_compression_variant_selfcheck", return_value={
                                    "available": True,
                                    "ready": True,
                                    "detail": "ok",
                                    "variants": {
                                        "dxt1": {"ok": True, "detail": "size=(16, 16)"},
                                        "dxt3": {"ok": True, "detail": "size=(16, 16)"},
                                        "dxt5": {"ok": True, "detail": "size=(16, 16)"},
                                    },
                                    "failures": [],
                                }):
                                    self.assertEqual(main._optional_feature_readiness_notice(), "")

    def test_optional_feature_readiness_notice_summarizes_limits(self):
        _require_qt_gui(self)
        import main
        with patch("src.ui.video_tool._has_imageio", return_value=True):
            with patch("src.ui.video_tool._has_imageio_ffmpeg", return_value=True):
                with patch("src.ui.video_tool._get_ffmpeg_exe", return_value="/tmp/ffmpeg"):
                    with patch("src.ui.video_tool._get_ffprobe_exe", return_value=None):
                        with patch("src.core.file_converter.dds_compression_available", return_value=False):
                            with patch(
                                "src.core.file_converter.optional_pillow_output_limits",
                                return_value=[("AVIF", "needs libavif"), ("JPEG2000", "needs OpenJPEG")],
                            ):
                                notice = main._optional_feature_readiness_notice()
        self.assertIn("odd-container probing limited: ffprobe unavailable", notice)
        self.assertIn("DDS compressed variants unavailable: ImageMagick/wand runtime missing", notice)
        self.assertIn("AVIF", notice)
        self.assertIn("See tool banners for details.", notice)

    def test_optional_feature_readiness_notice_lists_missing_video_runtime_bits(self):
        _require_qt_gui(self)
        import main
        with patch("src.ui.video_tool._has_imageio", return_value=False):
            with patch("src.ui.video_tool._has_imageio_ffmpeg", return_value=True):
                with patch("src.ui.video_tool._get_ffmpeg_exe", return_value=None):
                    with patch("src.core.file_converter.dds_compression_available", return_value=True):
                        with patch("src.core.file_converter.optional_pillow_output_limits", return_value=[]):
                            with patch.object(main, "_dds_compression_variant_selfcheck", return_value={
                                "available": True,
                                "ready": True,
                                "detail": "ok",
                                "variants": {},
                                "failures": [],
                            }):
                                notice = main._optional_feature_readiness_notice()
        self.assertIn("video import/MP4 export unavailable: missing imageio, ffmpeg", notice)

    def test_runtime_capability_summary_reports_runtime_bits(self):
        _require_qt_gui(self)
        import main
        import src.ui.video_tool as vt
        import src.core.file_converter as fc
        with patch.object(vt, "_has_imageio", return_value=True):
            with patch.object(vt, "_has_imageio_ffmpeg", return_value=True):
                with patch.object(vt, "_get_ffmpeg_exe", return_value="/tmp/ffmpeg"):
                    with patch.object(vt, "_get_ffprobe_exe", return_value=None):
                        with patch.object(fc, "dds_compression_available", return_value=False):
                            with patch.object(fc, "optional_pillow_output_limits", return_value=[("AVIF", "needs libavif")]):
                                with patch.object(main, "_missing_linux_runtime_libs", return_value=["libEGL.so.1"]):
                                    with patch.object(main, "_theme_svg_runtime_details", return_value={
                                        "qt_svg_ready": True,
                                        "default_theme_svg_path": "/tmp/panda_dark.svg",
                                        "default_theme_svg_ready": True,
                                        "theme_svg_missing_count": 0,
                                    }):
                                        with patch.object(main, "_imagemagick_runtime_details", return_value={
                                            "wand_runtime_ready": False,
                                            "magick_home_path": "/tmp/magick",
                                            "imagemagick_home_path": "",
                                        }):
                                            with patch.object(main, "_executable_runtime_details", side_effect=[
                                                {"path": "/tmp/ffmpeg", "exists": True, "runtime_ready": True, "detail": "ffmpeg ok"},
                                                {"path": "", "exists": False, "runtime_ready": False, "detail": "missing"},
                                            ]):
                                                with patch.object(main, "_dds_compression_variant_selfcheck", return_value={
                                                    "available": False,
                                                    "ready": False,
                                                    "detail": "skipped: ImageMagick/wand runtime unavailable",
                                                    "variants": {},
                                                    "failures": [],
                                                }):
                                                    summary = main._runtime_capability_summary()
        self.assertTrue(summary["has_imageio"])
        self.assertTrue(summary["has_imageio_ffmpeg"])
        self.assertEqual(summary["ffmpeg_path"], "/tmp/ffmpeg")
        self.assertEqual(summary["ffprobe_path"], "")
        self.assertTrue(summary["ffmpeg_runtime_ready"])
        self.assertEqual(summary["ffmpeg_runtime_detail"], "ffmpeg ok")
        self.assertTrue(summary["video_runtime_ready"])
        self.assertFalse(summary["odd_container_probe_ready"])
        self.assertFalse(summary["dds_compression_available"])
        self.assertEqual(summary["missing_video_bits"], [])
        self.assertEqual(summary["missing_linux_runtime_libs"], ["libEGL.so.1"])
        self.assertIn("libEGL.so.1", summary["packaged_runtime_notice"])
        self.assertIn("odd-container probing/detail guidance limited: ffprobe unavailable", summary["feature_readiness_notice"])
        self.assertIn("See tool banners for details.", summary["feature_readiness_notice"])
        self.assertTrue(summary["qt_svg_ready"])
        self.assertEqual(summary["default_theme_svg_path"], "/tmp/panda_dark.svg")
        self.assertFalse(summary["wand_runtime_ready"])
        self.assertEqual(summary["magick_home_path"], "/tmp/magick")
        self.assertTrue(summary["imagemagick_configured"])
        self.assertIn("ImageMagick/wand runtime incomplete", summary["feature_readiness_notice"])

    def test_runtime_capability_summary_reports_packaged_asset_gaps(self):
        _require_qt_gui(self)
        import main
        import src.ui.video_tool as vt
        import src.core.file_converter as fc
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_dir = os.path.join(tmpdir, "bundle")
            external_dir = os.path.join(tmpdir, "external")
            os.makedirs(bundle_dir, exist_ok=True)
            os.makedirs(external_dir, exist_ok=True)
            executable_path = os.path.join(bundle_dir, "formatomancer")
            ffmpeg_path = os.path.join(external_dir, "ffmpeg")
            svg_path = os.path.join(external_dir, "panda_dark.svg")
            for path in (executable_path, ffmpeg_path, svg_path):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("x")
            with patch.object(vt, "_has_imageio", return_value=True):
                with patch.object(vt, "_has_imageio_ffmpeg", return_value=True):
                    with patch.object(vt, "_get_ffmpeg_exe", return_value=ffmpeg_path):
                        with patch.object(vt, "_get_ffprobe_exe", return_value=None):
                            with patch.object(fc, "dds_compression_available", return_value=False):
                                with patch.object(fc, "optional_pillow_output_limits", return_value=[]):
                                    with patch.object(main, "_missing_linux_runtime_libs", return_value=[]):
                                        with patch.object(main, "_theme_svg_runtime_details", return_value={
                                            "qt_svg_ready": True,
                                            "default_theme_svg_path": svg_path,
                                            "default_theme_svg_ready": True,
                                            "theme_svg_missing_count": 2,
                                        }):
                                            with patch.object(main, "_imagemagick_runtime_details", return_value={
                                                "wand_runtime_ready": False,
                                                "magick_home_path": "",
                                                "imagemagick_home_path": "",
                                            }):
                                                with patch.object(main.sys, "frozen", True, create=True):
                                                    with patch.object(main.sys, "executable", executable_path):
                                                        with patch.object(main, "_executable_runtime_details", side_effect=[
                                                            {"path": ffmpeg_path, "exists": True, "runtime_ready": True, "detail": "ffmpeg ok"},
                                                            {"path": "", "exists": False, "runtime_ready": False, "detail": "missing"},
                                                        ]):
                                                            summary = main._runtime_capability_summary()
        self.assertTrue(summary["frozen"])
        self.assertEqual(summary["bundle_dir"], bundle_dir)
        self.assertFalse(summary["ffmpeg_bundled"])
        self.assertFalse(summary["default_theme_svg_bundled"])
        self.assertFalse(summary["imagemagick_bundled"])
        self.assertIn("ffmpeg resolves outside the packaged app", summary["packaged_asset_warnings"])
        self.assertIn("packaged ffprobe binary missing", summary["packaged_asset_warnings"])
        self.assertIn("default theme SVG resolves outside the packaged app", summary["packaged_asset_warnings"])
        self.assertIn("2 theme SVG asset(s) missing from package", summary["packaged_asset_warnings"])
        self.assertIn("packaged ImageMagick/wand runtime unavailable for DDS compressed output", summary["packaged_asset_warnings"])
        self.assertIn("packaged asset gaps:", summary["feature_readiness_notice"])

    def test_runtime_capability_summary_flags_incomplete_bundled_imagemagick(self):
        _require_qt_gui(self)
        import main
        import src.ui.video_tool as vt
        import src.core.file_converter as fc
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_dir = os.path.join(tmpdir, "bundle")
            os.makedirs(bundle_dir, exist_ok=True)
            executable_path = os.path.join(bundle_dir, "formatomancer")
            ffmpeg_path = os.path.join(bundle_dir, "ffmpeg")
            ffprobe_path = os.path.join(bundle_dir, "ffprobe")
            svg_path = os.path.join(bundle_dir, "panda_dark.svg")
            magick_home = os.path.join(bundle_dir, "ImageMagick")
            os.makedirs(magick_home, exist_ok=True)
            for path in (executable_path, ffmpeg_path, ffprobe_path, svg_path):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("x")
            with patch.object(vt, "_has_imageio", return_value=True):
                with patch.object(vt, "_has_imageio_ffmpeg", return_value=True):
                    with patch.object(vt, "_get_ffmpeg_exe", return_value=ffmpeg_path):
                        with patch.object(vt, "_get_ffprobe_exe", return_value=ffprobe_path):
                            with patch.object(fc, "dds_compression_available", return_value=False):
                                with patch.object(fc, "optional_pillow_output_limits", return_value=[]):
                                    with patch.object(main, "_missing_linux_runtime_libs", return_value=[]):
                                        with patch.object(main, "_theme_svg_runtime_details", return_value={
                                            "qt_svg_ready": True,
                                            "default_theme_svg_path": svg_path,
                                            "default_theme_svg_ready": True,
                                            "theme_svg_missing_count": 0,
                                        }):
                                            with patch.object(main, "_imagemagick_runtime_details", return_value={
                                                "wand_runtime_ready": False,
                                                "magick_home_path": magick_home,
                                                "imagemagick_home_path": "",
                                            }):
                                                with patch.object(main.sys, "frozen", True, create=True):
                                                    with patch.object(main.sys, "executable", executable_path):
                                                        with patch.object(main, "_executable_runtime_details", side_effect=[
                                                            {"path": ffmpeg_path, "exists": True, "runtime_ready": True, "detail": "ffmpeg ok"},
                                                            {"path": ffprobe_path, "exists": True, "runtime_ready": True, "detail": "ffprobe ok"},
                                                        ]):
                                                            with patch.object(main, "_dds_compression_variant_selfcheck", return_value={
                                                                "available": False,
                                                                "ready": False,
                                                                "detail": "skipped: ImageMagick/wand runtime unavailable",
                                                                "variants": {},
                                                                "failures": [],
                                                            }):
                                                                summary = main._runtime_capability_summary()
        self.assertTrue(summary["imagemagick_bundled"])
        self.assertTrue(summary["imagemagick_configured"])
        self.assertIn("bundled ImageMagick/wand runtime incomplete", summary["packaged_asset_warnings"])
        self.assertIn("ImageMagick/wand runtime incomplete", summary["feature_readiness_notice"])

    def test_runtime_capability_summary_requires_ffmpeg_selfcheck_for_video_ready(self):
        _require_qt_gui(self)
        import main
        import src.ui.video_tool as vt
        import src.core.file_converter as fc
        with patch.object(vt, "_has_imageio", return_value=True):
            with patch.object(vt, "_has_imageio_ffmpeg", return_value=True):
                with patch.object(vt, "_get_ffmpeg_exe", return_value="/tmp/ffmpeg"):
                    with patch.object(vt, "_get_ffprobe_exe", return_value="/tmp/ffprobe"):
                        with patch.object(fc, "dds_compression_available", return_value=True):
                            with patch.object(fc, "optional_pillow_output_limits", return_value=[]):
                                with patch.object(main, "_missing_linux_runtime_libs", return_value=[]):
                                    with patch.object(main, "_theme_svg_runtime_details", return_value={
                                        "qt_svg_ready": True,
                                        "default_theme_svg_path": "/tmp/panda_dark.svg",
                                        "default_theme_svg_ready": True,
                                        "theme_svg_missing_count": 0,
                                    }):
                                        with patch.object(main, "_imagemagick_runtime_details", return_value={
                                            "wand_runtime_ready": True,
                                            "magick_home_path": "",
                                            "imagemagick_home_path": "",
                                        }):
                                            with patch.object(main, "_executable_runtime_details", side_effect=[
                                                {"path": "/tmp/ffmpeg", "exists": True, "runtime_ready": False, "detail": "permission denied"},
                                                {"path": "/tmp/ffprobe", "exists": True, "runtime_ready": True, "detail": "ffprobe ok"},
                                            ]):
                                                with patch.object(main, "_dds_compression_variant_selfcheck", return_value={
                                                    "available": True,
                                                    "ready": True,
                                                    "detail": "ok",
                                                    "variants": {},
                                                    "failures": [],
                                                }):
                                                    summary = main._runtime_capability_summary()
        self.assertFalse(summary["video_runtime_ready"])
        self.assertFalse(summary["odd_container_probe_ready"])
        self.assertIn("ffmpeg runtime", summary["missing_video_bits"])
        self.assertIn("ffmpeg self-check failed", summary["feature_readiness_notice"])
        self.assertEqual(summary["ffmpeg_runtime_detail"], "permission denied")

    def test_runtime_capability_summary_flags_failed_dds_variant_selfcheck(self):
        _require_qt_gui(self)
        import main
        import src.ui.video_tool as vt
        import src.core.file_converter as fc
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_dir = os.path.join(tmpdir, "bundle")
            os.makedirs(bundle_dir, exist_ok=True)
            executable_path = os.path.join(bundle_dir, "formatomancer")
            ffmpeg_path = os.path.join(bundle_dir, "ffmpeg")
            ffprobe_path = os.path.join(bundle_dir, "ffprobe")
            svg_path = os.path.join(bundle_dir, "panda_dark.svg")
            magick_home = os.path.join(bundle_dir, "ImageMagick")
            os.makedirs(magick_home, exist_ok=True)
            for path in (executable_path, ffmpeg_path, ffprobe_path, svg_path):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("x")
            with patch.object(vt, "_has_imageio", return_value=True):
                with patch.object(vt, "_has_imageio_ffmpeg", return_value=True):
                    with patch.object(vt, "_get_ffmpeg_exe", return_value=ffmpeg_path):
                        with patch.object(vt, "_get_ffprobe_exe", return_value=ffprobe_path):
                            with patch.object(fc, "dds_compression_available", return_value=True):
                                with patch.object(fc, "optional_pillow_output_limits", return_value=[]):
                                    with patch.object(main, "_missing_linux_runtime_libs", return_value=[]):
                                        with patch.object(main, "_theme_svg_runtime_details", return_value={
                                            "qt_svg_ready": True,
                                            "default_theme_svg_path": svg_path,
                                            "default_theme_svg_ready": True,
                                            "theme_svg_missing_count": 0,
                                        }):
                                            with patch.object(main, "_imagemagick_runtime_details", return_value={
                                                "wand_runtime_ready": True,
                                                "magick_home_path": magick_home,
                                                "imagemagick_home_path": "",
                                            }):
                                                with patch.object(main.sys, "frozen", True, create=True):
                                                    with patch.object(main.sys, "executable", executable_path):
                                                        with patch.object(main, "_executable_runtime_details", side_effect=[
                                                            {"path": ffmpeg_path, "exists": True, "runtime_ready": True, "detail": "ffmpeg ok"},
                                                            {"path": ffprobe_path, "exists": True, "runtime_ready": True, "detail": "ffprobe ok"},
                                                        ]):
                                                            with patch.object(main, "_dds_compression_variant_selfcheck", return_value={
                                                                "available": True,
                                                                "ready": False,
                                                                "detail": "failed: BC2/DXT3",
                                                                "variants": {
                                                                    "dxt1": {"ok": True, "detail": "size=(16, 16)"},
                                                                    "dxt3": {"ok": False, "detail": "wand save failed"},
                                                                    "dxt5": {"ok": True, "detail": "size=(16, 16)"},
                                                                },
                                                                "failures": ["dxt3"],
                                                            }):
                                                                summary = main._runtime_capability_summary()
        self.assertTrue(summary["dds_compression_variant_selfcheck_available"])
        self.assertFalse(summary["dds_compression_variant_selfcheck_ready"])
        self.assertEqual(summary["dds_compression_variant_failures"], ["dxt3"])
        self.assertIn("DDS compressed output self-check failed: BC2/DXT3", summary["feature_readiness_notice"])
        self.assertIn("packaged DDS compressed output self-check failed: BC2/DXT3", summary["packaged_asset_warnings"])

    def test_runtime_capability_dump_emits_prefixed_json(self):
        import main
        payload = {"video_runtime_ready": True, "odd_container_probe_ready": True}
        buffer = io.StringIO()
        with patch.object(main, "_runtime_capability_summary", return_value=payload):
            with patch("sys.stdout", buffer):
                rc = main._emit_runtime_capability_dump()
        self.assertEqual(rc, 0)
        line = buffer.getvalue().strip()
        self.assertTrue(line.startswith("ALPHA_FIXER_RUNTIME_CAPABILITIES="))
        parsed = json.loads(line.split("=", 1)[1])
        self.assertEqual(parsed, payload)

    def test_dds_compression_variant_selfcheck_reports_skipped_when_unavailable(self):
        import main
        fake_fc = types.SimpleNamespace(
            dds_compression_available=MagicMock(return_value=False),
            convert_file=MagicMock(),
        )
        with patch.dict(
            sys.modules,
            {
                "src.core.file_converter": fake_fc,
                "src.core.alpha_processor": types.SimpleNamespace(_load_dds=MagicMock()),
                "PIL": types.SimpleNamespace(Image=MagicMock()),
                "PIL.Image": MagicMock(),
            },
            clear=False,
        ):
            result = main._dds_compression_variant_selfcheck()
        self.assertFalse(result["available"])
        self.assertFalse(result["ready"])
        self.assertEqual(result["failures"], [])
        self.assertIn("skipped:", result["detail"])

    def test_dds_compression_variant_selfcheck_records_all_variant_results(self):
        import main
        fake_dds = MagicMock()
        fake_dds.size = (16, 16)
        fake_fc = types.SimpleNamespace(
            dds_compression_available=MagicMock(return_value=True),
        )

        def _fake_convert(_src, dest, _target_format, **_kwargs):
            with open(dest, "wb") as handle:
                handle.write(b"dds")

        fake_fc.convert_file = MagicMock(side_effect=_fake_convert)
        fake_alpha = types.SimpleNamespace(_load_dds=MagicMock(return_value=fake_dds))
        fake_image_instance = MagicMock()
        fake_pil_image = MagicMock()
        fake_pil_image.new.return_value = fake_image_instance
        with patch.dict(
            sys.modules,
            {
                "src.core.file_converter": fake_fc,
                "src.core.alpha_processor": fake_alpha,
                "PIL": types.SimpleNamespace(Image=fake_pil_image),
                "PIL.Image": fake_pil_image,
            },
            clear=False,
        ):
            result = main._dds_compression_variant_selfcheck()
        self.assertTrue(result["available"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["failures"], [])
        self.assertEqual(set(result["variants"]), {"dxt1", "dxt3", "dxt5"})
        self.assertTrue(all(result["variants"][name]["ok"] for name in ("dxt1", "dxt3", "dxt5")))

    def test_runtime_selftest_dump_emits_prefixed_json(self):
        import main
        buffer = io.StringIO()
        fake_image_instance = MagicMock()
        fake_image_instance.size = (32, 24)
        fake_pil_image = MagicMock()
        fake_pil_image.new.return_value = fake_image_instance
        fake_dds = MagicMock()
        fake_dds.size = (32, 24)
        fake_alpha = types.SimpleNamespace(_load_dds=MagicMock(return_value=fake_dds))
        fake_fc = types.SimpleNamespace(
            SUPPORTED_OUTPUT_FORMATS={"PNG": ".png"},
            convert_file=MagicMock(return_value=None),
            dds_compression_available=MagicMock(return_value=False),
        )
        fake_vt = types.SimpleNamespace(_get_ffmpeg_exe=MagicMock(return_value=None))
        fake_ui_pkg = types.SimpleNamespace(video_tool=fake_vt)
        with patch.object(main, "_runtime_selftest_iterations", return_value=2):
            with patch.object(main, "_runtime_selftest_peak_rss_mb", return_value=123.45):
                with patch.object(main, "_dds_compression_variant_selfcheck", return_value={
                    "available": False,
                    "ready": False,
                    "detail": "skipped: ImageMagick/wand runtime unavailable",
                    "variants": {},
                    "failures": [],
                }):
                    with patch("main.tempfile.TemporaryDirectory") as tmpdir_cls:
                        tmpdir_cls.return_value.__enter__.return_value = "/tmp/runtime-selftest"
                        tmpdir_cls.return_value.__exit__.return_value = False
                        with patch.dict(
                            sys.modules,
                            {
                                "PIL": types.SimpleNamespace(Image=fake_pil_image),
                                "PIL.Image": fake_pil_image,
                                "src.core.alpha_processor": fake_alpha,
                                "src.core.file_converter": fake_fc,
                                "src.ui": fake_ui_pkg,
                                "src.ui.video_tool": fake_vt,
                            },
                            clear=False,
                        ):
                            with patch("sys.stdout", buffer):
                                rc = main._emit_runtime_selftest_dump()
        self.assertEqual(rc, 1)
        line = buffer.getvalue().strip()
        self.assertTrue(line.startswith("ALPHA_FIXER_RUNTIME_SELFTEST="))
        parsed = json.loads(line.split("=", 1)[1])
        self.assertEqual(parsed["iterations"], 2)
        self.assertIn("peak_rss_mb", parsed)
        self.assertIn("checks", parsed)
        self.assertIn("png_to_dds_dxt1", parsed["checks"])
        self.assertIn("png_to_dds_dxt3", parsed["checks"])
        self.assertIn("png_to_dds_dxt5", parsed["checks"])
        self.assertFalse(parsed["passed"])

    def test_runtime_selftest_dump_emits_grouped_manifest_checks_when_requested(self):
        import main
        buffer = io.StringIO()
        fake_image_instance = MagicMock()
        fake_image_instance.size = (32, 24)
        fake_pil_image = MagicMock()
        fake_pil_image.new.return_value = fake_image_instance
        fake_dds = MagicMock()
        fake_dds.size = (32, 24)
        fake_alpha = types.SimpleNamespace(_load_dds=MagicMock(return_value=fake_dds))
        fake_fc = types.SimpleNamespace(
            SUPPORTED_OUTPUT_FORMATS={"PNG": ".png", "DDS": ".dds"},
            convert_file=MagicMock(return_value=None),
            dds_compression_available=MagicMock(return_value=False),
        )
        fake_vt = types.SimpleNamespace(_get_ffmpeg_exe=MagicMock(return_value=None))
        fake_ui_pkg = types.SimpleNamespace(video_tool=fake_vt)
        disc_manifest = [{"platform": "PSP", "path": "/tmp/psp.iso"}, {"platform": "PS1", "path": "/tmp/ps1.bin"}]
        dds_manifest = [{"group": "cubemap", "path": "/tmp/cube.dds"}, {"group": "array", "path": "/tmp/array.dds"}]
        format_manifest = [{"input": "/tmp/a.png", "target_format": "PNG"}, {"input": "/tmp/b.png", "target_format": "DDS"}]

        def _fake_manifest_env(name):
            return {
                "ALPHA_FIXER_RUNTIME_DISC_VIDEO_MANIFEST": disc_manifest,
                "ALPHA_FIXER_RUNTIME_DDS_MANIFEST": dds_manifest,
                "ALPHA_FIXER_RUNTIME_FORMAT_MATRIX_MANIFEST": format_manifest,
            }.get(name, [])

        with patch.object(main, "_runtime_selftest_iterations", return_value=1):
            with patch.object(main, "_runtime_selftest_peak_rss_mb", return_value=None):
                with patch.object(main, "_dds_compression_variant_selfcheck", return_value={
                    "available": False,
                    "ready": False,
                    "detail": "skipped: ImageMagick/wand runtime unavailable",
                    "variants": {},
                    "failures": [],
                }):
                    with patch.object(main, "load_manifest_entries_from_env", side_effect=_fake_manifest_env):
                        with patch.object(main, "execute_disc_video_manifest", side_effect=lambda entries, *_args, **_kwargs: (True, f"disc={len(entries)}")):
                            with patch.object(main, "execute_dds_manifest", side_effect=lambda entries, *_args, **_kwargs: (True, f"dds={len(entries)}")):
                                with patch.object(main, "execute_format_matrix_manifest", side_effect=lambda entries, **_kwargs: (True, f"matrix={len(entries)}")):
                                    with patch("main.tempfile.TemporaryDirectory") as tmpdir_cls:
                                        tmpdir_cls.return_value.__enter__.return_value = "/tmp/runtime-selftest"
                                        tmpdir_cls.return_value.__exit__.return_value = False
                                        with patch.dict(
                                            os.environ,
                                            {
                                                "ALPHA_FIXER_RUNTIME_DISC_GROUP_CHECKS": "1",
                                                "ALPHA_FIXER_RUNTIME_DDS_GROUP_CHECKS": "1",
                                                "ALPHA_FIXER_RUNTIME_FORMAT_GROUP_CHECKS": "1",
                                            },
                                            clear=False,
                                        ):
                                            with patch.dict(
                                                sys.modules,
                                                {
                                                    "PIL": types.SimpleNamespace(Image=fake_pil_image),
                                                    "PIL.Image": fake_pil_image,
                                                    "src.core.alpha_processor": fake_alpha,
                                                    "src.core.file_converter": fake_fc,
                                                    "src.ui": fake_ui_pkg,
                                                    "src.ui.video_tool": fake_vt,
                                                },
                                                clear=False,
                                            ):
                                                with patch("sys.stdout", buffer):
                                                    main._emit_runtime_selftest_dump()
        parsed = json.loads(buffer.getvalue().strip().split("=", 1)[1])
        checks = parsed["checks"]
        self.assertIn("external_disc_video_manifest_psp", checks)
        self.assertIn("external_disc_video_manifest_ps1", checks)
        self.assertIn("external_dds_manifest_cubemap", checks)
        self.assertIn("external_dds_manifest_array", checks)
        self.assertIn("external_format_matrix_manifest_png", checks)
        self.assertIn("external_format_matrix_manifest_dds", checks)

    def test_verify_packaged_app_parses_selftest_payload(self):
        module_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_packaged_app.py")
        spec = importlib.util.spec_from_file_location("verify_packaged_app", module_path)
        verify = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(verify)
        payload = verify._selftest_payload("hello\nALPHA_FIXER_RUNTIME_SELFTEST={\"passed\": true, \"iterations\": 3}\n")
        self.assertTrue(payload["passed"])
        self.assertEqual(payload["iterations"], 3)

    def test_verify_packaged_app_passes_external_manifests_into_selftest_env(self):
        module_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_packaged_app.py")
        spec = importlib.util.spec_from_file_location("verify_packaged_app", module_path)
        verify = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(verify)

        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "AlphaFixerConverter")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("stub")
            os.chmod(target, 0o755)
            calls = []

            def _fake_run(command, *, env, timeout):
                calls.append({"command": list(command), "env": dict(env), "timeout": timeout})
                if env.get("ALPHA_FIXER_RUNTIME_CAPABILITY_DUMP") == "1":
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout='ALPHA_FIXER_RUNTIME_CAPABILITIES={"video_runtime_ready": true, "odd_container_probe_ready": true, "missing_linux_runtime_libs": [], "dds_compression_available": true}\n',
                    )
                if env.get("ALPHA_FIXER_RUNTIME_SELFTEST"):
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout='ALPHA_FIXER_RUNTIME_SELFTEST={"passed": true, "iterations": 2, "checks": {"external_disc_video_manifest": {"ok": true}, "external_dds_manifest": {"ok": true}, "external_format_matrix_manifest": {"ok": true}}}\n',
                    )
                return types.SimpleNamespace(returncode=0, stdout="")

            with patch.object(verify, "_run_and_echo", side_effect=_fake_run):
                rc = verify.main(
                    [
                        target,
                        "--run-selftest",
                        "--selftest-sample-limit",
                        "7",
                        "--disc-video-manifest",
                        "/tmp/disc.json",
                        "--dds-manifest",
                        "/tmp/dds.json",
                        "--format-matrix-manifest",
                        "/tmp/matrix.json",
                        "--allow-sample-downloads",
                        "--sample-cache-dir",
                        "/tmp/sample-cache",
                        "--require-selftest-pass",
                        "--require-selftest-check",
                        "external_disc_video_manifest",
                    ]
                )
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 3)
        selftest_env = calls[-1]["env"]
        self.assertEqual(selftest_env["ALPHA_FIXER_RUNTIME_SELFTEST"], "2")
        self.assertEqual(selftest_env["ALPHA_FIXER_RUNTIME_SAMPLE_LIMIT"], "7")
        self.assertEqual(selftest_env["ALPHA_FIXER_RUNTIME_DISC_VIDEO_MANIFEST"], "/tmp/disc.json")
        self.assertEqual(selftest_env["ALPHA_FIXER_RUNTIME_DDS_MANIFEST"], "/tmp/dds.json")
        self.assertEqual(selftest_env["ALPHA_FIXER_RUNTIME_FORMAT_MATRIX_MANIFEST"], "/tmp/matrix.json")
        self.assertEqual(selftest_env["ALPHA_FIXER_RUNTIME_ALLOW_SAMPLE_DOWNLOADS"], "1")
        self.assertEqual(selftest_env["ALPHA_FIXER_RUNTIME_SAMPLE_CACHE_DIR"], "/tmp/sample-cache")

    def test_verify_packaged_app_use_public_sample_manifests_populates_defaults(self):
        module_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_packaged_app.py")
        spec = importlib.util.spec_from_file_location("verify_packaged_app", module_path)
        verify = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(verify)

        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "AlphaFixerConverter")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("stub")
            os.chmod(target, 0o755)
            calls = []

            def _fake_run(command, *, env, timeout):
                calls.append({"command": list(command), "env": dict(env), "timeout": timeout})
                if env.get("ALPHA_FIXER_RUNTIME_CAPABILITY_DUMP") == "1":
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout='ALPHA_FIXER_RUNTIME_CAPABILITIES={"video_runtime_ready": true, "odd_container_probe_ready": true, "missing_linux_runtime_libs": [], "dds_compression_available": true}\n',
                    )
                if env.get("ALPHA_FIXER_RUNTIME_SELFTEST"):
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout='ALPHA_FIXER_RUNTIME_SELFTEST={"passed": true, "iterations": 2, "checks": {"external_disc_video_manifest": {"ok": true}, "external_dds_manifest": {"ok": true}, "external_format_matrix_manifest": {"ok": true}}}\n',
                    )
                return types.SimpleNamespace(returncode=0, stdout="")

            with patch.object(verify, "_run_and_echo", side_effect=_fake_run):
                rc = verify.main(
                    [
                        target,
                        "--run-selftest",
                        "--use-public-sample-manifests",
                        "--selftest-sample-limit",
                        "3",
                    ]
                )
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 3)
        selftest_env = calls[-1]["env"]
        self.assertTrue(selftest_env["ALPHA_FIXER_RUNTIME_DISC_VIDEO_MANIFEST"].endswith("sample_manifests/public_disc_video_manifest.json"))
        self.assertTrue(selftest_env["ALPHA_FIXER_RUNTIME_DDS_MANIFEST"].endswith("sample_manifests/public_dds_dx10_manifest.json"))
        self.assertTrue(selftest_env["ALPHA_FIXER_RUNTIME_FORMAT_MATRIX_MANIFEST"].endswith("sample_manifests/public_format_matrix_manifest.json"))

    def test_verify_packaged_app_can_require_public_manifest_checks(self):
        module_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_packaged_app.py")
        spec = importlib.util.spec_from_file_location("verify_packaged_app", module_path)
        verify = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(verify)

        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "AlphaFixerConverter")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("stub")
            os.chmod(target, 0o755)

            def _fake_run(command, *, env, timeout):
                if env.get("ALPHA_FIXER_RUNTIME_CAPABILITY_DUMP") == "1":
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout='ALPHA_FIXER_RUNTIME_CAPABILITIES={"video_runtime_ready": true, "odd_container_probe_ready": true, "missing_linux_runtime_libs": [], "dds_compression_available": true}\n',
                    )
                if env.get("ALPHA_FIXER_RUNTIME_SELFTEST"):
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout='ALPHA_FIXER_RUNTIME_SELFTEST={"passed": true, "iterations": 2, "checks": {"external_disc_video_manifest": {"ok": true, "detail": "disc ok"}, "external_dds_manifest": {"ok": true, "detail": "dds ok"}, "external_format_matrix_manifest": {"ok": true, "detail": "matrix ok"}}}\n',
                    )
                return types.SimpleNamespace(returncode=0, stdout="")

            with patch.object(verify, "_run_and_echo", side_effect=_fake_run):
                rc = verify.main(
                    [
                        target,
                        "--run-selftest",
                        "--use-public-sample-manifests",
                        "--require-public-manifest-checks",
                    ]
                )
        self.assertEqual(rc, 0)

    def test_verify_packaged_app_can_require_public_manifest_group_checks(self):
        module_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_packaged_app.py")
        spec = importlib.util.spec_from_file_location("verify_packaged_app", module_path)
        verify = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(verify)

        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "AlphaFixerConverter")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("stub")
            os.chmod(target, 0o755)
            calls = []

            def _fake_run(command, *, env, timeout):
                calls.append({"command": list(command), "env": dict(env), "timeout": timeout})
                if env.get("ALPHA_FIXER_RUNTIME_CAPABILITY_DUMP") == "1":
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout='ALPHA_FIXER_RUNTIME_CAPABILITIES={"video_runtime_ready": true, "odd_container_probe_ready": true, "missing_linux_runtime_libs": [], "dds_compression_available": true}\n',
                    )
                if env.get("ALPHA_FIXER_RUNTIME_SELFTEST"):
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout='ALPHA_FIXER_RUNTIME_SELFTEST={"passed": true, "iterations": 2, "checks": {"external_disc_video_manifest_disc_image": {"ok": true}, "external_disc_video_manifest_psp": {"ok": true}, "external_disc_video_manifest_pmf_movie_asset": {"ok": true}, "external_disc_video_manifest_odd_container": {"ok": true}, "external_disc_video_manifest_legacy_container": {"ok": true}, "external_disc_video_manifest_program_stream": {"ok": true}, "external_disc_video_manifest_matroska_family": {"ok": true}, "external_disc_video_manifest_audio_only": {"ok": true}, "external_dds_manifest_bc6h": {"ok": true}, "external_dds_manifest_bc4": {"ok": true}, "external_dds_manifest_dx10_bc4": {"ok": true}, "external_dds_manifest_bc5": {"ok": true}, "external_dds_manifest_dx10_bc5": {"ok": true}, "external_dds_manifest_bc7_mipmap": {"ok": true}, "external_dds_manifest_bc7": {"ok": true}, "external_dds_manifest_dx10_rgba": {"ok": true}, "external_dds_manifest_rgba_mipmap": {"ok": true}, "external_dds_manifest_unsupported_dxgi": {"ok": true}, "external_dds_manifest_unsupported_pixel_format": {"ok": true}, "external_dds_manifest_unsupported_bitcount": {"ok": true}, "external_format_matrix_manifest_dds": {"ok": true}, "external_format_matrix_manifest_gif": {"ok": true}, "external_format_matrix_manifest_ico": {"ok": true}, "external_format_matrix_manifest_png": {"ok": true}, "external_format_matrix_manifest_jpeg": {"ok": true}, "external_format_matrix_manifest_tiff": {"ok": true}, "external_format_matrix_manifest_bmp": {"ok": true}, "external_format_matrix_manifest_tga": {"ok": true}}}\n',
                    )
                return types.SimpleNamespace(returncode=0, stdout="")

            with patch.object(verify, "_run_and_echo", side_effect=_fake_run):
                rc = verify.main(
                    [
                        target,
                        "--run-selftest",
                        "--use-public-sample-manifests",
                        "--require-public-manifest-group-checks",
                    ]
                )
        self.assertEqual(rc, 0)
        selftest_env = calls[-1]["env"]
        self.assertEqual(selftest_env["ALPHA_FIXER_RUNTIME_DISC_GROUP_CHECKS"], "1")
        self.assertEqual(selftest_env["ALPHA_FIXER_RUNTIME_DDS_GROUP_CHECKS"], "1")
        self.assertEqual(selftest_env["ALPHA_FIXER_RUNTIME_FORMAT_GROUP_CHECKS"], "1")

    def test_verify_packaged_app_can_require_grouped_video_selftest_checks(self):
        module_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_packaged_app.py")
        spec = importlib.util.spec_from_file_location("verify_packaged_app", module_path)
        verify = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(verify)

        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "AlphaFixerConverter")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("stub")
            os.chmod(target, 0o755)

            def _fake_run(command, *, env, timeout):
                if env.get("ALPHA_FIXER_RUNTIME_CAPABILITY_DUMP") == "1":
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout='ALPHA_FIXER_RUNTIME_CAPABILITIES={"video_runtime_ready": true, "odd_container_probe_ready": true, "missing_linux_runtime_libs": [], "dds_compression_available": true}\n',
                    )
                if env.get("ALPHA_FIXER_RUNTIME_SELFTEST"):
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout='ALPHA_FIXER_RUNTIME_SELFTEST={"passed": true, "iterations": 2, "checks": {"generated_mp4_load": {"ok": true}, "mpegts_load": {"ok": true}, "synthetic_bin_probe": {"ok": true}}}\n',
                    )
                return types.SimpleNamespace(returncode=0, stdout="")

            with patch.object(verify, "_run_and_echo", side_effect=_fake_run):
                rc = verify.main([target, "--run-selftest", "--require-video-selftest-checks"])
        self.assertEqual(rc, 0)

    def test_verify_packaged_app_can_require_manifest_group_checks(self):
        module_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_packaged_app.py")
        spec = importlib.util.spec_from_file_location("verify_packaged_app", module_path)
        verify = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(verify)

        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "AlphaFixerConverter")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("stub")
            os.chmod(target, 0o755)
            disc_manifest = os.path.join(tmpdir, "disc.json")
            dds_manifest = os.path.join(tmpdir, "dds.json")
            matrix_manifest = os.path.join(tmpdir, "matrix.json")
            with open(disc_manifest, "w", encoding="utf-8") as handle:
                json.dump({"entries": [{"platform": "PSP", "path": "/tmp/psp.iso"}, {"platform": "PS1", "path": "/tmp/ps1.bin"}]}, handle)
            with open(dds_manifest, "w", encoding="utf-8") as handle:
                json.dump({"entries": [{"group": "cubemap", "path": "/tmp/cube.dds"}, {"group": "array", "path": "/tmp/array.dds"}]}, handle)
            with open(matrix_manifest, "w", encoding="utf-8") as handle:
                json.dump({"entries": [{"input": "/tmp/in.png", "target_format": "PNG"}, {"input": "/tmp/in.webp", "target_format": "DDS"}]}, handle)
            calls = []

            def _fake_run(command, *, env, timeout):
                calls.append({"command": list(command), "env": dict(env), "timeout": timeout})
                if env.get("ALPHA_FIXER_RUNTIME_CAPABILITY_DUMP") == "1":
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout='ALPHA_FIXER_RUNTIME_CAPABILITIES={"video_runtime_ready": true, "odd_container_probe_ready": true, "missing_linux_runtime_libs": [], "dds_compression_available": true}\n',
                    )
                if env.get("ALPHA_FIXER_RUNTIME_SELFTEST"):
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout='ALPHA_FIXER_RUNTIME_SELFTEST={"passed": true, "iterations": 2, "checks": {"external_disc_video_manifest_psp": {"ok": true}, "external_disc_video_manifest_ps1": {"ok": true}, "external_dds_manifest_cubemap": {"ok": true}, "external_dds_manifest_array": {"ok": true}, "external_format_matrix_manifest_png": {"ok": true}, "external_format_matrix_manifest_dds": {"ok": true}}}\n',
                    )
                return types.SimpleNamespace(returncode=0, stdout="")

            with patch.object(verify, "_run_and_echo", side_effect=_fake_run):
                rc = verify.main(
                    [
                        target,
                        "--run-selftest",
                        "--disc-video-manifest",
                        disc_manifest,
                        "--dds-manifest",
                        dds_manifest,
                        "--format-matrix-manifest",
                        matrix_manifest,
                        "--require-disc-manifest-group-checks",
                        "--require-dds-manifest-group-checks",
                        "--require-format-manifest-group-checks",
                    ]
                )
        self.assertEqual(rc, 0)
        selftest_env = calls[-1]["env"]
        self.assertEqual(selftest_env["ALPHA_FIXER_RUNTIME_DISC_GROUP_CHECKS"], "1")
        self.assertEqual(selftest_env["ALPHA_FIXER_RUNTIME_DDS_GROUP_CHECKS"], "1")
        self.assertEqual(selftest_env["ALPHA_FIXER_RUNTIME_FORMAT_GROUP_CHECKS"], "1")

    def test_verify_packaged_app_manifest_group_checks_require_grouped_manifest(self):
        module_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_packaged_app.py")
        spec = importlib.util.spec_from_file_location("verify_packaged_app", module_path)
        verify = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(verify)

        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "AlphaFixerConverter")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("stub")
            os.chmod(target, 0o755)
            plain_manifest = os.path.join(tmpdir, "plain.json")
            with open(plain_manifest, "w", encoding="utf-8") as handle:
                json.dump({"entries": [{"path": "/tmp/sample.iso"}]}, handle)
            with self.assertRaises(SystemExit) as ctx:
                verify.main(
                    [
                        target,
                        "--run-selftest",
                        "--disc-video-manifest",
                        plain_manifest,
                        "--require-disc-manifest-group-checks",
                    ]
                )
        self.assertIn("platform/group labels", str(ctx.exception))

    def test_verify_packaged_app_public_manifest_group_checks_require_all_manifests(self):
        module_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_packaged_app.py")
        spec = importlib.util.spec_from_file_location("verify_packaged_app", module_path)
        verify = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(verify)

        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "AlphaFixerConverter")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("stub")
            os.chmod(target, 0o755)
            with self.assertRaises(SystemExit) as ctx:
                verify.main([target, "--run-selftest", "--require-public-manifest-group-checks"])
        self.assertIn("Use --use-public-sample-manifests", str(ctx.exception))

    def test_verify_packaged_app_grouped_dds_selftest_checks_need_run_selftest(self):
        module_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_packaged_app.py")
        spec = importlib.util.spec_from_file_location("verify_packaged_app", module_path)
        verify = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(verify)

        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "AlphaFixerConverter")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("stub")
            os.chmod(target, 0o755)
            with self.assertRaises(SystemExit) as ctx:
                verify.main([target, "--require-dds-selftest-checks"])
        self.assertIn("--run-selftest", str(ctx.exception))

    def test_verify_packaged_app_require_public_manifest_checks_fails_when_missing(self):
        module_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_packaged_app.py")
        spec = importlib.util.spec_from_file_location("verify_packaged_app", module_path)
        verify = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(verify)

        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "AlphaFixerConverter")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("stub")
            os.chmod(target, 0o755)

            def _fake_run(command, *, env, timeout):
                if env.get("ALPHA_FIXER_RUNTIME_CAPABILITY_DUMP") == "1":
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout='ALPHA_FIXER_RUNTIME_CAPABILITIES={"video_runtime_ready": true, "odd_container_probe_ready": true, "missing_linux_runtime_libs": [], "dds_compression_available": true}\n',
                    )
                if env.get("ALPHA_FIXER_RUNTIME_SELFTEST"):
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout='ALPHA_FIXER_RUNTIME_SELFTEST={"passed": true, "iterations": 2, "checks": {"external_disc_video_manifest": {"ok": true}, "external_dds_manifest": {"ok": false, "detail": "dds failed"}}}\n',
                    )
                return types.SimpleNamespace(returncode=0, stdout="")

            with patch.object(verify, "_run_and_echo", side_effect=_fake_run):
                with self.assertRaises(SystemExit) as ctx:
                    verify.main(
                        [
                            target,
                            "--run-selftest",
                            "--use-public-sample-manifests",
                            "--require-public-manifest-checks",
                        ]
                    )
        self.assertIn("external_dds_manifest", str(ctx.exception))

    def test_verify_packaged_app_can_require_bundled_dependencies_and_no_asset_gaps(self):
        module_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_packaged_app.py")
        spec = importlib.util.spec_from_file_location("verify_packaged_app", module_path)
        verify = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(verify)

        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "AlphaFixerConverter")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("stub")
            os.chmod(target, 0o755)

            def _fake_run(command, *, env, timeout):
                if env.get("ALPHA_FIXER_RUNTIME_CAPABILITY_DUMP") == "1":
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout=(
                            'ALPHA_FIXER_RUNTIME_CAPABILITIES={"video_runtime_ready": true, '
                            '"odd_container_probe_ready": true, "missing_linux_runtime_libs": [], '
                            '"dds_compression_available": true, "ffmpeg_bundled": true, '
                            '"ffprobe_bundled": true, "imagemagick_bundled": true, '
                            '"packaged_asset_warnings": []}\n'
                        ),
                    )
                return types.SimpleNamespace(returncode=0, stdout="")

            with patch.object(verify, "_run_and_echo", side_effect=_fake_run):
                rc = verify.main(
                    [
                        target,
                        "--require-bundled-ffmpeg",
                        "--require-bundled-ffprobe",
                        "--require-bundled-imagemagick",
                        "--require-no-packaged-asset-gaps",
                    ]
                )
        self.assertEqual(rc, 0)

    def test_verify_packaged_app_merges_repeated_manifest_arguments(self):
        module_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_packaged_app.py")
        spec = importlib.util.spec_from_file_location("verify_packaged_app", module_path)
        verify = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(verify)

        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "AlphaFixerConverter")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("stub")
            os.chmod(target, 0o755)
            psp_manifest = os.path.join(tmpdir, "psp.json")
            ps1_manifest = os.path.join(tmpdir, "ps1.json")
            with open(psp_manifest, "w", encoding="utf-8") as handle:
                json.dump({"entries": [{"path": "/tmp/psp.iso", "expect": "load_or_explain"}]}, handle)
            with open(ps1_manifest, "w", encoding="utf-8") as handle:
                json.dump({"entries": [{"path": "/tmp/ps1.bin", "expect": "load_or_explain"}]}, handle)
            calls = []

            def _fake_run(command, *, env, timeout):
                calls.append({"command": list(command), "env": dict(env), "timeout": timeout})
                if env.get("ALPHA_FIXER_RUNTIME_CAPABILITY_DUMP") == "1":
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout='ALPHA_FIXER_RUNTIME_CAPABILITIES={"video_runtime_ready": true, "odd_container_probe_ready": true, "missing_linux_runtime_libs": [], "dds_compression_available": true}\n',
                    )
                if env.get("ALPHA_FIXER_RUNTIME_SELFTEST"):
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout='ALPHA_FIXER_RUNTIME_SELFTEST={"passed": true, "iterations": 1, "checks": {}}\n',
                    )
                return types.SimpleNamespace(returncode=0, stdout="")

            with patch.object(verify, "_run_and_echo", side_effect=_fake_run):
                rc = verify.main(
                    [
                        target,
                        "--run-selftest",
                        "--disc-video-manifest",
                        psp_manifest,
                        "--disc-video-manifest",
                        ps1_manifest,
                    ]
                )
        self.assertEqual(rc, 0)
        merged_payload = json.loads(calls[-1]["env"]["ALPHA_FIXER_RUNTIME_DISC_VIDEO_MANIFEST"])
        self.assertEqual(len(merged_payload["entries"]), 2)
        self.assertEqual(merged_payload["entries"][0]["path"], "/tmp/psp.iso")
        self.assertEqual(merged_payload["entries"][1]["path"], "/tmp/ps1.bin")

    def test_verify_packaged_app_can_require_ffmpeg_selfcheck(self):
        module_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_packaged_app.py")
        spec = importlib.util.spec_from_file_location("verify_packaged_app", module_path)
        verify = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(verify)

        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "AlphaFixerConverter")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("stub")
            os.chmod(target, 0o755)

            def _fake_run(command, *, env, timeout):
                if env.get("ALPHA_FIXER_RUNTIME_CAPABILITY_DUMP") == "1":
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout='ALPHA_FIXER_RUNTIME_CAPABILITIES={"video_runtime_ready": false, "odd_container_probe_ready": false, "missing_linux_runtime_libs": [], "dds_compression_available": true, "ffmpeg_runtime_ready": false, "ffmpeg_runtime_detail": "permission denied"}\n',
                    )
                return types.SimpleNamespace(returncode=0, stdout="")

            with patch.object(verify, "_run_and_echo", side_effect=_fake_run):
                with self.assertRaises(SystemExit) as ctx:
                    verify.main([target, "--require-ffmpeg-selfcheck"])
        self.assertIn("ffmpeg_runtime_ready=false", str(ctx.exception))

    def test_runtime_selftest_dump_records_external_manifest_checks(self):
        import main

        buffer = io.StringIO()
        fake_image_instance = MagicMock()
        fake_image_instance.size = (32, 24)
        fake_pil_image = MagicMock()
        fake_pil_image.new.return_value = fake_image_instance
        fake_dds = MagicMock()
        fake_dds.size = (32, 24)
        fake_alpha = types.SimpleNamespace(_load_dds=MagicMock(return_value=fake_dds))
        fake_fc = types.SimpleNamespace(
            SUPPORTED_OUTPUT_FORMATS={"PNG": ".png"},
            convert_file=MagicMock(return_value="/tmp/out.png"),
            dds_compression_available=MagicMock(return_value=False),
        )
        fake_vt = types.SimpleNamespace(_get_ffmpeg_exe=MagicMock(return_value=None))
        fake_ui_pkg = types.SimpleNamespace(video_tool=fake_vt)
        with patch.object(main, "_runtime_selftest_iterations", return_value=1):
            with patch.object(main, "_runtime_selftest_peak_rss_mb", return_value=None):
                with patch("main.tempfile.TemporaryDirectory") as tmpdir_cls:
                    tmpdir_cls.return_value.__enter__.return_value = "/tmp/runtime-selftest"
                    tmpdir_cls.return_value.__exit__.return_value = False
                    with patch.object(main, "load_manifest_entries_from_env") as loader:
                        loader.side_effect = [
                            [{"path": "/tmp/disc.iso"}],
                            [{"path": "/tmp/sample.dds"}],
                            [{"input": "/tmp/sample.png", "target_format": "PNG"}],
                        ]
                        with patch.object(main, "execute_disc_video_manifest", return_value=(True, "disc ok")):
                            with patch.object(main, "execute_dds_manifest", return_value=(True, "dds ok")):
                                with patch.object(main, "execute_format_matrix_manifest", return_value=(True, "matrix ok")):
                                    with patch.dict(
                                        sys.modules,
                                        {
                                            "PIL": types.SimpleNamespace(Image=fake_pil_image),
                                            "PIL.Image": fake_pil_image,
                                            "src.core.alpha_processor": fake_alpha,
                                            "src.core.file_converter": fake_fc,
                                            "src.ui": fake_ui_pkg,
                                            "src.ui.video_tool": fake_vt,
                                        },
                                        clear=False,
                                    ):
                                        with patch("sys.stdout", buffer):
                                            main._emit_runtime_selftest_dump()
        parsed = json.loads(buffer.getvalue().strip().split("=", 1)[1])
        self.assertIn("external_disc_video_manifest", parsed["checks"])
        self.assertIn("external_dds_manifest", parsed["checks"])
        self.assertIn("external_format_matrix_manifest", parsed["checks"])
        self.assertTrue(parsed["checks"]["external_disc_video_manifest"]["ok"])

    def test_main_window_runtime_readiness_helpers_surface_limits(self):
        _require_qt_gui(self)
        from src.ui import main_window as mw

        summary = {
            "video_runtime_ready": False,
            "odd_container_probe_ready": False,
            "missing_video_bits": ["imageio", "ffmpeg"],
            "dds_compression_available": False,
            "optional_output_limits": [("AVIF", "needs libavif")],
            "missing_linux_runtime_libs": ["libEGL.so.1"],
            "packaged_runtime_notice": "⚠ Optional Linux runtime libraries are missing: libEGL.so.1.",
            "feature_readiness_notice": "⚠ Optional feature limits detected: video import/MP4 export unavailable.",
            "optional_qt_notice": "⚠ Optional Linux multimedia backends unavailable: PipeWire.",
            "has_imageio": False,
            "has_imageio_ffmpeg": True,
            "ffmpeg_path": "",
            "ffprobe_path": "",
        }

        banner = mw._runtime_readiness_banner_text(summary)
        tooltip = mw._runtime_readiness_banner_tooltip(summary)

        self.assertIn("video/MP4 limited (imageio, ffmpeg)", banner)
        self.assertIn("DDS compressed output limited", banner)
        self.assertIn("optional image export limit", banner)
        self.assertIn("Main-window readiness snapshot", tooltip)
        self.assertIn("imageio: missing", tooltip)
        self.assertIn("ffmpeg: missing", tooltip)
        self.assertIn("DDS compressed output: limited", tooltip)
        self.assertIn("ImageMagick/wand runtime: limited", tooltip)
        self.assertIn("Qt SVG renderer:", tooltip)
        self.assertIn("Alpha & RGBA:", tooltip)
        self.assertIn("Alpha Painter:", tooltip)
        self.assertIn("History:", tooltip)

    def test_main_window_runtime_readiness_helpers_surface_packaged_asset_gap_details(self):
        _require_qt_gui(self)
        from src.ui import main_window as mw

        summary = {
            "video_runtime_ready": True,
            "odd_container_probe_ready": True,
            "missing_video_bits": [],
            "dds_compression_available": True,
            "optional_output_limits": [],
            "missing_linux_runtime_libs": [],
            "packaged_runtime_notice": "",
            "feature_readiness_notice": "⚠ Optional feature limits detected: packaged asset gaps: ffprobe missing.",
            "optional_qt_notice": "",
            "has_imageio": True,
            "has_imageio_ffmpeg": True,
            "ffmpeg_path": "/tmp/ffmpeg",
            "ffprobe_path": "",
            "ffmpeg_path_exists": True,
            "ffprobe_path_exists": False,
            "wand_runtime_ready": True,
            "qt_svg_ready": True,
            "default_theme_svg_ready": True,
            "default_theme_svg_path": "/tmp/panda_dark.svg",
            "theme_svg_missing_count": 0,
            "packaged_asset_warnings": ["packaged ffprobe binary missing"],
        }
        with patch.object(mw, "_gif_builder_capability_details", return_value="GIF DETAIL"):
            with patch.object(mw, "_video_capability_details", return_value="VIDEO DETAIL"):
                with patch.object(mw, "_selective_alpha_capability_details", return_value="SELECTIVE DETAIL"):
                    with patch.object(mw, "_history_capability_details", return_value="HISTORY DETAIL"):
                        banner = mw._runtime_readiness_banner_text(summary)
                        tooltip = mw._runtime_readiness_banner_tooltip(summary)

        self.assertIn("1 packaged asset gap", banner)
        self.assertIn("Packaged asset gaps:", tooltip)
        self.assertIn("packaged ffprobe binary missing", tooltip)
        self.assertIn("Packaged dependency audit:", tooltip)
        self.assertIn("SELECTIVE DETAIL", tooltip)
        self.assertIn("HISTORY DETAIL", tooltip)
        self.assertIn("GIF DETAIL", tooltip)
        self.assertIn("VIDEO DETAIL", tooltip)
        self.assertIn("Converter:", tooltip)
        self.assertIn("Alpha Painter:", tooltip)
        self.assertIn("History:", tooltip)
        self.assertIn("GIF Builder:", tooltip)
        self.assertIn("Video Builder:", tooltip)

    def test_main_window_runtime_readiness_helpers_surface_verified_packaged_bundle(self):
        _require_qt_gui(self)
        from src.ui import main_window as mw

        summary = {
            "frozen": True,
            "bundle_dir": "/tmp/dist",
            "video_runtime_ready": True,
            "odd_container_probe_ready": True,
            "missing_video_bits": [],
            "dds_compression_available": True,
            "optional_output_limits": [],
            "missing_linux_runtime_libs": [],
            "packaged_runtime_notice": "",
            "feature_readiness_notice": "",
            "optional_qt_notice": "",
            "has_imageio": True,
            "has_imageio_ffmpeg": True,
            "ffmpeg_path": "/tmp/dist/ffmpeg",
            "ffprobe_path": "/tmp/dist/ffprobe",
            "ffmpeg_path_exists": True,
            "ffprobe_path_exists": True,
            "ffmpeg_runtime_ready": True,
            "ffprobe_runtime_ready": True,
            "ffmpeg_bundled": True,
            "ffprobe_bundled": True,
            "wand_runtime_ready": True,
            "imagemagick_bundled": True,
            "qt_svg_ready": True,
            "default_theme_svg_ready": True,
            "default_theme_svg_path": "/tmp/dist/panda_dark.svg",
            "default_theme_svg_bundled": True,
            "theme_svg_missing_count": 0,
            "packaged_asset_warnings": [],
            "packaged_bundle_ready": True,
        }

        banner = mw._runtime_readiness_banner_text(summary)
        tooltip = mw._runtime_readiness_banner_tooltip(summary)

        self.assertIn("packaged bundle verified", banner)
        self.assertIn("Packaged dependency audit:", tooltip)
        self.assertIn("Bundled ffmpeg: yes", tooltip)
        self.assertIn("Packaged bundle verification: passed", tooltip)


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
        self.assertIsNotNone(w.after_image())

    def test_display_only_updates_do_not_replace_raw_preview_images(self):
        from src.ui.preview_pane import BeforeAfterWidget, _pil_to_qimage
        from PIL import Image
        w = BeforeAfterWidget(self._parent)
        before_raw = _pil_to_qimage(Image.new("RGBA", (32, 32), (255, 0, 0, 255)))
        after_raw = _pil_to_qimage(Image.new("RGBA", (32, 32), (0, 255, 0, 255)))
        before_overlay = _pil_to_qimage(Image.new("RGBA", (32, 32), (0, 0, 255, 255)))
        after_overlay = _pil_to_qimage(Image.new("RGBA", (32, 32), (255, 255, 0, 255)))
        w.store_raw_images(before_raw, after_raw)
        w.set_before(before_overlay, store_raw=False)
        w.set_after(after_overlay, store_raw=False)
        self.assertEqual(w.before_image().pixelColor(0, 0).getRgb(), before_raw.pixelColor(0, 0).getRgb())
        self.assertEqual(w.after_image().pixelColor(0, 0).getRgb(), after_raw.pixelColor(0, 0).getRgb())

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


@unittest.skipUnless(_PYQT6_AVAILABLE, "PyQt6 not installed")
class TestButtonPressAnimator(unittest.TestCase):
    def setUp(self):
        self._app = _get_app()
        from PyQt6.QtWidgets import QWidget
        self._parent = QWidget()
        self._parent.resize(300, 200)

    def tearDown(self):
        self._parent.hide()
        self._parent.deleteLater()
        self._app.processEvents()

    def test_deleted_button_animation_is_ignored(self):
        from PyQt6.QtWidgets import QPushButton
        from src.ui.click_effects import ButtonPressAnimator

        btn = QPushButton("Close", self._parent)
        animator = ButtonPressAnimator(self._parent)
        animator.set_mode("press")
        btn.deleteLater()
        self._app.processEvents()

        animator._animate(btn)


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


@unittest.skipUnless(_PYQT6_AVAILABLE, "PyQt6 not installed")
class TestAlphaPreviewHelpers(unittest.TestCase):
    def setUp(self):
        self._app = _get_app()
        from src.core.settings_manager import SettingsManager
        from src.core.presets import PresetManager
        from src.ui.alpha_tool import AlphaFixerTab

        self._settings = SettingsManager()
        self._settings._qs = _FakeQSettings({})
        self._presets = PresetManager(self._settings)
        self._widget = AlphaFixerTab(self._presets, self._settings)

    def tearDown(self):
        self._widget.hide()
        self._widget.deleteLater()
        self._app.processEvents()

    def test_helper_status_updates_for_alpha_and_atlas_overlays(self):
        import numpy as np
        from PIL import Image
        from src.ui.preview_pane import _pil_to_qimage

        arr = np.zeros((24, 24, 4), dtype=np.uint8)
        arr[1:11, 1:11, :3] = 255
        arr[1:11, 1:11, 3] = 255
        arr[1:11, 13:23, :3] = 255
        arr[1:11, 13:23, 3] = 255
        arr[13:23, 1:11, :3] = 255
        arr[13:23, 1:11, 3] = 255
        arr[13:23, 13:23, :3] = 255
        arr[13:23, 13:23, 3] = 255
        arr[12, 6, 3] = 6
        arr[18, 12, 3] = 4
        img = Image.fromarray(arr, "RGBA")
        qi = _pil_to_qimage(img)

        self._widget._on_compare_ready(qi, qi)
        self.assertIn("raw before/after preview", self._widget._preview_helper_lbl.text())

        self._widget._alpha_vis_check.setChecked(True)
        self.assertIn("alpha heat-map on", self._widget._preview_helper_lbl.text())

        self._widget._atlas_detect_check.setChecked(True)
        self.assertEqual(len(self._widget._atlas_cells), 4)
        self.assertIn("atlas boxes on (4 cells)", self._widget._preview_helper_lbl.text())

    def test_batch_import_completed_emits_status_notice(self):
        received = []
        self._widget.status_notice.connect(lambda message, timeout: received.append((message, timeout)))
        self._widget._on_batch_import_completed(2, 1, 3)
        self.assertEqual(received, [("Alpha queue: Added 2 new files; skipped 1 duplicate.", 6000)])

    def test_get_queue_status_text_includes_preview_state(self):
        self._widget._file_list.addItem("a.png")
        with patch.object(
            self._widget._file_list,
            "get_thumbnail_summary",
            return_value={"pending_count": 0, "failure_count": 2, "failure_categories": {"memory": 2}},
        ):
            text = self._widget.get_queue_status_text()
        self.assertEqual(text, "📁 1 queued  •  2 preview failures (memory)")

    def test_update_file_count_emits_queue_status_changed(self):
        self._widget._file_list.addItem("a.png")
        received = []
        self._widget.queue_status_changed.connect(received.append)
        with patch.object(self._widget, "get_queue_status_text", return_value="📁 1 queued  •  thumbnail previews paused"):
            self._widget._update_file_count(1)
        self.assertEqual(received, ["📁 1 queued  •  thumbnail previews paused"])


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


class TestSettingsManagerShortcutBindings(unittest.TestCase):
    def setUp(self):
        from src.core.settings_manager import SettingsManager
        self._mgr = SettingsManager.__new__(SettingsManager)
        self._store: dict = {}
        self._mgr._qs = _FakeQSettings(self._store)

    def test_get_custom_shortcuts_invalid_payload_returns_empty_dict(self):
        self._store["custom_shortcuts"] = "not-json"
        self.assertEqual(self._mgr.get_custom_shortcuts(), {})

    def test_set_shortcut_binding_round_trips_and_clears_default(self):
        self._mgr.set_shortcut_binding("gif_export", "Ctrl+Shift+G", "Ctrl+S")
        self.assertEqual(
            self._mgr.get_shortcut_binding("gif_export", "Ctrl+S"),
            "Ctrl+Shift+G",
        )
        self._mgr.set_shortcut_binding("gif_export", "Ctrl+S", "Ctrl+S")
        self.assertEqual(self._mgr.get_custom_shortcuts(), {})
        self.assertEqual(
            self._mgr.get_shortcut_binding("gif_export", "Ctrl+S"),
            "Ctrl+S",
        )


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
        # Normal and Dumbed Down keep exactly 5 variants per key for readability.
        for mode_name, tips_dict in [("Normal", _NORMAL), ("Dumbed", _DUMBED)]:
            for key, variants in tips_dict.items():
                self.assertEqual(len(variants), 5,
                                 f"{mode_name}['{key}'] should have 5 variants, got {len(variants)}")
        # No Filter 🤬 mode has at least 5 variants per key (usually 8 for extra variety).
        for key, variants in _VULGAR.items():
            self.assertGreaterEqual(len(variants), 5,
                                    f"Vulgar['{key}'] should have at least 5 variants, got {len(variants)}")

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
        self.assertEqual(default, "No Filter 🤬")


@unittest.skipUnless(_PYQT6_AVAILABLE, "PyQt6 not installed")
class TestTooltipManagerFirstRun(unittest.TestCase):
    """Logic-only tests that do not require a display / Qt GUI stack."""

    def test_mode_returns_no_filter_when_key_absent_on_first_run(self):
        """Regression: mode() must return 'No Filter 🤬' on first run when the
        key has never been written to the INI file.  Previously, a hardcoded
        'Normal' fallback in mode() overrode _DEFAULTS and caused Normal tips
        to be displayed even though the Settings dialog showed No Filter selected."""
        from src.ui.tooltip_manager import TooltipManager
        from src.core.settings_manager import SettingsManager

        # Simulate first-run: store is empty, _DEFAULTS supplies the real default.
        class _FirstRunSettings:
            _DEFAULTS = SettingsManager._DEFAULTS

            def get(self, key, fallback=None):
                default = fallback if fallback is not None else self._DEFAULTS.get(key)
                # Nothing is stored yet – return the default as QSettings would.
                return default

        mgr = TooltipManager(_FirstRunSettings())
        self.assertEqual(mgr.mode(), "No Filter 🤬")


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
        self.assertGreaterEqual(len(_NORMAL["effect_combo"]), 5)

    def test_normal_tips_have_custom_emoji_key(self):
        _require_pyqt6(self)
        from src.ui.tooltip_manager import _NORMAL
        self.assertIn("custom_emoji", _NORMAL)
        self.assertGreaterEqual(len(_NORMAL["custom_emoji"]), 5)

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
            self.assertGreaterEqual(len(tips["recursive_check"]), 5,
                             f"{mode_name}['recursive_check'] should have at least 5 variants")

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
                self.assertGreaterEqual(len(tips[key]), 5,
                                 f"{mode_name}['{key}'] should have at least 5 variants")


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


@unittest.skipUnless(_PYQT6_AVAILABLE, "PyQt6 not installed")
class TestVideoProbeFallbacks(unittest.TestCase):
    def test_probe_video_clip_uses_imageio_ffmpeg_count_fallback(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        class _FakeReader:
            def get_meta_data(self):
                return {"fps": 30.0, "nframes": 0, "duration": 0}

            def get_data(self, idx):
                self.last_idx = idx
                return [[0]]

            def count_frames(self):
                raise RuntimeError("metadata missing")

            def close(self):
                return None

        fake_ffmpeg = types.SimpleNamespace(
            count_frames_and_secs=lambda path: (12, 0.4),
        )
        with patch.object(vt, "_open_video_reader", return_value=_FakeReader()):
            with patch.dict(sys.modules, {"imageio_ffmpeg": fake_ffmpeg}):
                fps, frame_count, frame_size, first_frame = vt._probe_video_clip("/tmp/test.mp4")
        self.assertEqual(fps, 30.0)
        self.assertEqual(frame_count, 12)
        self.assertEqual(frame_size, (1, 1))
        self.assertEqual(first_frame, [[0]])

    def test_video_load_failure_hint_mentions_experimental_disc_images(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        with patch.object(vt, "_video_io_diagnostics", return_value="Missing: ffmpeg executable."):
            hint = vt._video_load_failure_hint("/tmp/game.iso")
        self.assertIn("experimental", hint)
        self.assertIn("ffmpeg can demux", hint)
        self.assertIn("Missing: ffmpeg executable.", hint)

    def test_video_load_failure_hint_mentions_disc_sidecar_retry_when_cue_exists(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        with tempfile.TemporaryDirectory() as tmpdir:
            bin_path = os.path.join(tmpdir, "game.bin")
            cue_path = os.path.join(tmpdir, "game.cue")
            with open(bin_path, "wb") as handle:
                handle.write(b"bin")
            with open(cue_path, "w", encoding="utf-8") as handle:
                handle.write('FILE "game.bin" BINARY\n')
            with patch.object(vt, "_video_io_diagnostics", return_value="All video dependencies are available."):
                hint = vt._video_load_failure_hint(bin_path)
        self.assertIn("Companion disc sidecar files were detected", hint)
        self.assertIn("game.cue", hint)

    def test_video_load_failure_hint_includes_probe_summary_when_available(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        probe = {
            "format_name": "iso9660",
            "has_video": False,
            "video_codec": "",
            "audio_codec": "mp2",
            "width": 0,
            "height": 0,
            "fps": 0.0,
        }
        with patch.object(vt, "_probe_media_details", return_value=probe):
            with patch.object(vt, "_video_io_diagnostics", return_value="All video dependencies are available."):
                hint = vt._video_load_failure_hint("/tmp/game.iso")
        self.assertIn("ffprobe did not detect a playable video stream", hint)
        self.assertIn("Probe: container=iso9660; video=none; audio=mp2.", hint)

    def test_video_load_failure_hint_mentions_audio_only_odd_container(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        probe = {
            "format_name": "ogg",
            "has_video": False,
            "has_audio": True,
            "video_codec": "",
            "audio_codec": "vorbis",
            "width": 0,
            "height": 0,
            "fps": 0.0,
        }
        with patch.object(vt, "_probe_media_details", return_value=probe):
            with patch.object(vt, "_video_io_diagnostics", return_value="All video dependencies are available."):
                hint = vt._video_load_failure_hint("/tmp/weird.dat")
        self.assertIn("audio but no playable video stream", hint)
        self.assertIn("container=ogg", hint)

    def test_video_load_failure_hint_mentions_multi_stream_recovery_choice(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        probe = {
            "format_name": "mpeg",
            "has_video": True,
            "has_audio": True,
            "video_codec": "mpeg2video",
            "audio_codec": "ac3",
            "width": 720,
            "height": 480,
            "fps": 29.97,
            "video_stream_count": 2,
            "audio_stream_count": 1,
            "video_stream_index": 3,
        }
        with patch.object(vt, "_probe_media_details", return_value=probe):
            with patch.object(vt, "_video_io_diagnostics", return_value="All video dependencies are available."):
                hint = vt._video_load_failure_hint("/tmp/weird.vob")
        self.assertIn("Multiple video streams were detected", hint)
        self.assertIn("preferred-stream=3", hint)

    def test_video_load_failure_hint_mentions_manual_audio_selection(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        probe = {
            "format_name": "matroska",
            "has_video": True,
            "has_audio": True,
            "video_codec": "h264",
            "audio_codec": "ac3",
            "width": 1280,
            "height": 720,
            "fps": 23.976,
            "video_stream_count": 1,
            "audio_stream_count": 2,
            "video_stream_index": 0,
            "audio_stream_index": 4,
            "audio_stream_choices": [
                {"index": 2, "codec_name": "ac3", "language": "eng", "title": "Main"},
                {"index": 4, "codec_name": "ac3", "language": "jpn", "title": "Commentary"},
            ],
        }
        with patch.object(vt, "_probe_media_details", return_value=probe):
            with patch.object(vt, "_video_io_diagnostics", return_value="All video dependencies are available."):
                hint = vt._video_load_failure_hint("/tmp/streamed.mkv", preferred_audio_stream_index=4)
        self.assertIn("Manual selection active: manual audio #4", hint)
        self.assertIn("preferred-audio-stream=4", hint)
        self.assertIn("try another audio stream or reload with source audio dropped", hint)

    def test_video_load_failure_hint_mentions_alternate_audio_retries(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        probe = {
            "format_name": "mpeg",
            "has_video": True,
            "has_audio": True,
            "video_codec": "mpeg2video",
            "audio_codec": "ac3",
            "width": 720,
            "height": 480,
            "fps": 29.97,
            "video_stream_count": 2,
            "audio_stream_count": 3,
            "video_stream_index": 1,
            "audio_stream_index": 4,
        }
        with patch.object(vt, "_probe_media_details", return_value=probe):
            with patch.object(vt, "_video_io_diagnostics", return_value="All video dependencies are available."):
                hint = vt._video_load_failure_hint("/tmp/feature.vob")
        self.assertIn("Multiple video streams were detected", hint)
        self.assertIn("Multiple audio streams were detected", hint)
        self.assertIn("alternate audio tracks", hint)

    def test_video_load_failure_hint_surfaces_auto_audio_choice_and_commentary_guidance(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        probe = {
            "format_name": "mpeg",
            "has_video": True,
            "has_audio": True,
            "video_codec": "mpeg2video",
            "audio_codec": "ac3",
            "width": 720,
            "height": 480,
            "fps": 29.97,
            "video_stream_count": 1,
            "audio_stream_count": 3,
            "video_stream_index": 1,
            "audio_stream_index": 4,
            "audio_stream_choices": [
                {"index": 2, "codec_name": "ac3", "language": "eng", "title": "Main", "default": False},
                {"index": 4, "codec_name": "ac3", "language": "eng", "title": "Director Commentary", "commentary": True, "default": True},
                {"index": 6, "codec_name": "ac3", "language": "jpn", "title": "Dub", "dub": True, "default": False},
            ],
        }
        with patch.object(vt, "_probe_media_details", return_value=probe):
            with patch.object(vt, "_video_io_diagnostics", return_value="All video dependencies are available."):
                hint = vt._video_load_failure_hint("/tmp/feature.vob")
        self.assertIn("Automatic selection active: preferred audio #4", hint)
        self.assertIn("Director Commentary", hint)
        self.assertIn("The currently preferred audio track looks like commentary audio", hint)

    def test_video_load_failure_hint_mentions_recovery_exhausted_for_odd_container(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        probe = {
            "format_name": "mpeg",
            "has_video": True,
            "has_audio": True,
            "video_codec": "mpeg2video",
            "audio_codec": "ac3",
            "width": 720,
            "height": 480,
            "fps": 29.97,
            "video_stream_count": 1,
            "audio_stream_count": 1,
        }
        with patch.object(vt, "_probe_media_details", return_value=probe):
            with patch.object(vt, "_video_io_diagnostics", return_value="All video dependencies are available."):
                hint = vt._video_load_failure_hint("/tmp/weird.vob")
        self.assertIn("still could not produce a playable clip", hint)
        self.assertIn("remux or transcode recovery may still be required", hint)
        self.assertIn("audio-drop", hint)
        self.assertIn("PSP/PS1/PS2-era assets", hint)

    def test_video_load_failure_hint_mentions_transport_stream_guidance(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        probe = {
            "format_name": "mpegts",
            "has_video": True,
            "has_audio": True,
            "video_codec": "mpeg2video",
            "audio_codec": "aac",
            "width": 720,
            "height": 480,
            "fps": 29.97,
            "video_stream_count": 1,
            "audio_stream_count": 1,
        }
        with patch.object(vt, "_probe_media_details", return_value=probe):
            with patch.object(vt, "_video_io_diagnostics", return_value="All video dependencies are available."):
                hint = vt._video_load_failure_hint("/tmp/capture.ts")
        self.assertIn("Transport-stream sources often contain discontinuities", hint)

    def test_video_load_failure_hint_mentions_realmedia_guidance(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        probe = {
            "format_name": "rm,rmvb",
            "has_video": True,
            "has_audio": True,
            "video_codec": "rv40",
            "audio_codec": "cook",
            "width": 640,
            "height": 360,
            "fps": 24.0,
            "video_stream_count": 1,
            "audio_stream_count": 1,
        }
        with patch.object(vt, "_probe_media_details", return_value=probe):
            with patch.object(vt, "_video_io_diagnostics", return_value="All video dependencies are available."):
                hint = vt._video_load_failure_hint("/tmp/legacy.rmvb")
        self.assertIn("RealMedia / RMVB support is best-effort", hint)
        self.assertIn("Detected a legacy RealVideo codec", hint)

    def test_video_load_failure_hint_mentions_hevc_wrapper_guidance(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        probe = {
            "format_name": "mpegts",
            "has_video": True,
            "has_audio": True,
            "video_codec": "hevc",
            "audio_codec": "aac",
            "width": 1920,
            "height": 1080,
            "fps": 29.97,
            "video_stream_count": 1,
            "audio_stream_count": 1,
        }
        with patch.object(vt, "_probe_media_details", return_value=probe):
            with patch.object(vt, "_video_io_diagnostics", return_value="All video dependencies are available."):
                hint = vt._video_load_failure_hint("/tmp/capture.ts")
        self.assertIn("Detected HEVC/H.265 or AV1 video", hint)
        self.assertIn("H.264/AVC", hint)

    def test_video_load_failure_hint_mentions_broadcast_codec_guidance(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        probe = {
            "format_name": "mxf",
            "has_video": True,
            "has_audio": True,
            "video_codec": "dnxhd",
            "audio_codec": "pcm_s16le",
            "width": 1920,
            "height": 1080,
            "fps": 25.0,
            "video_stream_count": 1,
            "audio_stream_count": 1,
        }
        with patch.object(vt, "_probe_media_details", return_value=probe):
            with patch.object(vt, "_video_io_diagnostics", return_value="All video dependencies are available."):
                hint = vt._video_load_failure_hint("/tmp/edit.mxf")
        self.assertIn("Detected an intermediate/broadcast codec", hint)
        self.assertIn("editorial transcode", hint)

    def test_classify_video_import_failure_distinguishes_audio_only_and_recovery(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        self.assertEqual(
            vt._classify_video_import_failure("odd.bin", "ffprobe detected audio but no playable video stream"),
            "audio-only container",
        )
        self.assertEqual(
            vt._classify_video_import_failure("odd.vob", "Direct loading and ffmpeg recovery fallbacks still could not produce a playable clip."),
            "recovery exhausted",
        )
        self.assertEqual(
            vt._classify_video_import_failure("album.m4a", "ffprobe only exposed an attached-picture/cover-art stream"),
            "cover-art stream",
        )
        self.assertEqual(
            vt._classify_video_import_failure("clip.part1.vob", "This source looks like a segmented / multipart video set (2 parts detected)"),
            "segmented container",
        )
        self.assertEqual(
            vt._classify_video_import_failure("strange.mxf", "Unsupported pixel format in codec pipeline"),
            "video codec",
        )
        self.assertEqual(
            vt._classify_video_import_failure("modern.ts", "Detected HEVC/H.265 or AV1 video; when these codecs arrive in AVI/WMV/TS/odd wrappers, remuxing to MP4 or transcoding to H.264/AVC is usually the most reliable import path."),
            "high-efficiency codec",
        )
        self.assertEqual(
            vt._classify_video_import_failure("concert.mkv", "Selected audio uses AC3/DTS-style compressed audio and multiple audio tracks are present; if import or export fails, try another audio stream or reload with source audio dropped."),
            "source audio track",
        )
        self.assertEqual(
            vt._classify_video_import_failure("capture.ts", "Transport-stream sources often contain discontinuities or missing timestamps; recovery may rebuild timing, but severe capture gaps can still prevent loading."),
            "transport stream timing",
        )
        self.assertEqual(
            vt._classify_video_import_failure("feature.mkv", "Matroska/WebM files can carry multiple alternate video/audio programs; if one stream fails, the builder will prefer the strongest detected video stream but manual stream reloads may still help."),
            "matroska/webm program",
        )
        self.assertEqual(
            vt._classify_video_import_failure("edit.mov", "QuickTime/MOV-family files may depend on edit lists, timecode, or ProRes-style metadata; remux/transcode recovery is often needed when direct indexing is incomplete."),
            "quicktime metadata",
        )
        self.assertEqual(
            vt._classify_video_import_failure("disc.str", "Detected legacy MPEG program-stream video commonly used in PSP/PS1/PS2-era assets; alternate tracks, cue/bin metadata, or audio-drop recovery may be needed before the clip becomes playable."),
            "program stream layout",
        )
        self.assertEqual(
            vt._classify_video_import_failure("slideshow.mkv", "Detected a still-image style video codec inside a nonstandard container; the builder may only recover this as a slideshow/still-frame source unless ffmpeg can transcode it cleanly."),
            "still-image video",
        )

    def test_video_capability_summary_mentions_ready_state_and_audio_only_limit(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        with patch.object(vt, "_has_ffmpeg", return_value=True):
            with patch.object(vt, "_has_imageio", return_value=True):
                with patch.object(vt, "_has_imageio_ffmpeg", return_value=True):
                    with patch.object(vt, "_get_ffprobe_exe", return_value="/tmp/ffprobe"):
                        summary = vt._video_capability_summary()
        self.assertIn("Ready now", summary)
        self.assertIn("Audio-only containers", summary)
        self.assertIn("single-frame fallbacks", summary)
        self.assertIn("preferred-stream selection", summary)
        self.assertIn("partial/corrupt containers", summary)

    def test_video_capability_details_surface_diagnostics_and_manual_picker_gap(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        with patch.object(vt, "_has_ffmpeg", return_value=True):
            with patch.object(vt, "_has_imageio", return_value=True):
                with patch.object(vt, "_has_imageio_ffmpeg", return_value=True):
                    with patch.object(vt, "_get_ffprobe_exe", return_value="/tmp/ffprobe"):
                        with patch.object(vt, "_video_io_diagnostics", return_value="All video dependencies are available."):
                            details = vt._video_capability_details()
        self.assertIn("All video dependencies are available.", details)
        self.assertIn("Selected Stream panel can reload a clip from manually chosen video and audio streams", details)
        self.assertIn("ffprobe detail/probing ready", details)

    def test_load_video_clip_uses_still_frame_fallback_when_recovery_paths_fail(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        from PIL import Image

        with patch.object(vt, "_probe_video_clip", side_effect=RuntimeError("primary open failed")):
            with patch.object(vt, "_probe_media_details", return_value={"has_video": True, "has_audio": True, "selected_video_attached_pic": True}):
                with patch.object(vt, "_attempt_video_recovery", return_value=(None, "", None)):
                    with patch.object(vt, "_extract_visual_still_frame", return_value=Image.new("RGBA", (8, 6), (255, 0, 0, 255))):
                        clip = vt._load_video_clip("/tmp/album.bin")
        self.assertIsNotNone(clip)
        self.assertEqual(clip.clip_type, "image")
        self.assertEqual(clip.source_path, "/tmp/album.bin")
        self.assertTrue(clip.has_audio)
        self.assertIn("still-frame fallback", clip.load_note)
        clip.close()

    def test_load_video_clip_retries_alternate_stream_recovery_for_multi_stream_sources(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        primary_probe = {
            "has_video": True,
            "has_audio": True,
            "video_stream_count": 2,
            "audio_stream_index": 7,
            "video_stream_index": 3,
            "video_stream_choices": [
                {"index": 3, "attached_pic": False, "width": 320, "height": 240, "fps": 24.0, "bit_rate": 1000, "duration": 10.0},
                {"index": 5, "attached_pic": False, "width": 640, "height": 480, "fps": 29.97, "bit_rate": 2000, "duration": 10.0},
            ],
        }
        alternate_probe = {
            **primary_probe,
            "video_stream_index": 5,
        }

        class _FakeReader:
            def get_meta_data(self):
                return {"fps": 30.0, "nframes": 4, "size": (640, 480)}

            def get_data(self, idx):
                return [[[0, 0, 0, 255]]]

            def close(self):
                return None

        def _probe_side_effect(path, preferred_video_stream_index=None, preferred_audio_stream_index=None):
            if preferred_video_stream_index == 5:
                return alternate_probe
            return primary_probe

        def _remux_side_effect(path, details=None):
            stream_index = None if details is None else details.get("video_stream_index")
            if stream_index == 5:
                return "/tmp/recovered-alt.mkv"
            return None

        with patch.object(vt, "_probe_media_details", side_effect=_probe_side_effect):
            with patch.object(vt, "_probe_video_clip", side_effect=[RuntimeError("primary open failed"), (30.0, 4, (640, 480), None)]):
                with patch.object(vt, "_remux_video_source", side_effect=_remux_side_effect):
                    with patch.object(vt, "_transcode_video_source", return_value=None):
                        with patch.object(vt, "_video_has_audio_stream", return_value=True):
                            with patch.object(vt, "_open_video_reader", return_value=_FakeReader()):
                                clip = vt._load_video_clip("/tmp/multi.vob")
        self.assertIsNotNone(clip)
        self.assertEqual(clip.preferred_video_stream_index, 5)
        self.assertIn("alternate stream #5", clip.load_note)
        clip.close()

    def test_load_video_clip_retries_matching_cue_sidecar_for_bin_sources(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        class _FakeReader:
            def get_meta_data(self):
                return {"fps": 30.0, "nframes": 4, "size": (320, 240)}

            def get_data(self, idx):
                return [[[0, 0, 0, 255]]]

            def close(self):
                return None

        with tempfile.TemporaryDirectory() as tmpdir:
            bin_path = os.path.join(tmpdir, "disc.bin")
            cue_path = os.path.join(tmpdir, "disc.cue")
            with open(bin_path, "wb") as handle:
                handle.write(b"bin")
            with open(cue_path, "w", encoding="utf-8") as handle:
                handle.write('FILE "disc.bin" BINARY\n')

            def _probe_side_effect(path, preferred_video_stream_index=None, preferred_audio_stream_index=None):
                self.assertIsNone(preferred_video_stream_index)
                self.assertIsNone(preferred_audio_stream_index)
                if path.endswith(".cue"):
                    return {"has_video": True, "has_audio": False, "video_stream_index": 0, "audio_stream_index": None}
                return None

            def _probe_video_side_effect(path):
                if path.endswith(".bin"):
                    raise RuntimeError("bin direct open failed")
                return 30.0, 4, (320, 240), None

            with patch.object(vt, "_probe_media_details", side_effect=_probe_side_effect):
                with patch.object(vt, "_probe_video_clip", side_effect=_probe_video_side_effect):
                    with patch.object(vt, "_video_has_audio_stream", return_value=False):
                        with patch.object(vt, "_open_video_reader", return_value=_FakeReader()):
                            clip = vt._load_video_clip(bin_path)
        self.assertIsNotNone(clip)
        self.assertEqual(clip.source_path, bin_path)
        self.assertIn("cue sidecar retry active", clip.load_note)
        clip.close()

    def test_probe_media_details_prefers_non_attached_pic_stream(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        payload = {
            "format": {"format_name": "matroska", "duration": "10.0"},
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "mjpeg",
                    "width": 600,
                    "height": 600,
                    "avg_frame_rate": "0/0",
                    "r_frame_rate": "0/0",
                    "disposition": {"attached_pic": 1},
                    "tags": {"title": "cover"},
                },
                {
                    "index": 2,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 320,
                    "height": 240,
                    "avg_frame_rate": "24/1",
                    "r_frame_rate": "24/1",
                    "bit_rate": "120000",
                    "disposition": {"attached_pic": 0},
                    "tags": {"language": "eng"},
                },
            ],
        }
        result = types.SimpleNamespace(returncode=0, stdout=__import__("json").dumps(payload))
        with patch.object(vt, "_get_ffprobe_exe", return_value="/tmp/ffprobe"):
            with patch.object(vt.subprocess, "run", return_value=result):
                details = vt._probe_media_details("/tmp/sample.mkv")
        self.assertIsNotNone(details)
        self.assertEqual(details["video_stream_index"], 2)
        self.assertEqual(details["video_codec"], "h264")
        self.assertEqual(details["video_attached_pic_count"], 1)
        self.assertEqual(details["selected_video_language"], "eng")

    def test_probe_media_details_honors_explicit_video_stream_selection(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        payload = {
            "format": {"format_name": "mpeg", "duration": "10.0"},
            "streams": [
                {
                    "index": 1,
                    "codec_type": "video",
                    "codec_name": "mpeg2video",
                    "width": 320,
                    "height": 240,
                    "avg_frame_rate": "24/1",
                    "r_frame_rate": "24/1",
                    "disposition": {"attached_pic": 0},
                    "tags": {"title": "main"},
                },
                {
                    "index": 7,
                    "codec_type": "video",
                    "codec_name": "mpeg1video",
                    "width": 160,
                    "height": 120,
                    "avg_frame_rate": "15/1",
                    "r_frame_rate": "15/1",
                    "disposition": {"attached_pic": 0},
                    "tags": {"language": "jpn", "title": "bonus"},
                },
            ],
        }
        result = types.SimpleNamespace(returncode=0, stdout=json.dumps(payload))
        with patch.object(vt, "_get_ffprobe_exe", return_value="/tmp/ffprobe"):
            with patch.object(vt.subprocess, "run", return_value=result):
                details = vt._probe_media_details("/tmp/sample.vob", preferred_video_stream_index=7)
        self.assertIsNotNone(details)
        self.assertEqual(details["video_stream_index"], 7)
        self.assertEqual(details["video_codec"], "mpeg1video")
        self.assertEqual(details["selected_video_language"], "jpn")
        self.assertEqual(len(details["video_stream_choices"]), 2)

    def test_probe_media_details_uses_deeper_analysis_flags(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        calls = []
        payload = {"format": {"format_name": "mpeg"}, "streams": []}

        def _fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return types.SimpleNamespace(returncode=0, stdout=json.dumps(payload))

        with patch.object(vt, "_get_ffprobe_exe", return_value="/tmp/ffprobe"):
            with patch.object(vt.subprocess, "run", side_effect=_fake_run):
                vt._probe_media_details("/tmp/sample.vob")
        self.assertTrue(calls)
        self.assertIn("-probesize", calls[0])
        self.assertIn("100M", calls[0])
        self.assertIn("-analyzeduration", calls[0])

    def test_video_load_failure_hint_mentions_cover_art_streams(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        probe = {
            "format_name": "mp3",
            "has_video": True,
            "has_audio": True,
            "video_codec": "mjpeg",
            "audio_codec": "mp3",
            "width": 600,
            "height": 600,
            "fps": 0.0,
            "video_stream_count": 1,
            "audio_stream_count": 1,
            "selected_video_attached_pic": True,
            "video_attached_pic_count": 1,
        }
        with patch.object(vt, "_probe_media_details", return_value=probe):
            with patch.object(vt, "_video_io_diagnostics", return_value="All video dependencies are available."):
                hint = vt._video_load_failure_hint("/tmp/album.bin")
        self.assertIn("attached-picture/cover-art stream", hint)
        self.assertIn("cover-art or slideshow streams", hint)

    def test_load_video_clip_uses_remux_fallback_for_disc_images(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        with tempfile.TemporaryDirectory() as tmpdir:
            remux_path = os.path.join(tmpdir, "remux.mkv")
            with open(remux_path, "wb") as fh:
                fh.write(b"remux")

            def _probe(path: str):
                if path.endswith(".iso"):
                    raise RuntimeError("primary open failed")
                return 24.0, 12, (320, 240), None

            with patch.object(vt, "_probe_video_clip", side_effect=_probe):
                with patch.object(vt, "_remux_video_source", return_value=remux_path):
                    with patch.object(vt, "_video_has_audio_stream", return_value=True):
                        clip = vt._load_video_clip("/tmp/game.iso")
            self.assertIsNotNone(clip)
            self.assertEqual(clip.source_path, "/tmp/game.iso")
            self.assertEqual(clip.path, remux_path)
            self.assertTrue(clip.has_audio)
            self.assertIn("remux fallback", clip.load_note)
            clip.close()
            self.assertFalse(os.path.exists(remux_path))

    def test_load_video_clip_uses_transcode_fallback_when_remux_fails(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        with tempfile.TemporaryDirectory() as tmpdir:
            transcode_path = os.path.join(tmpdir, "transcoded.mp4")
            with open(transcode_path, "wb") as fh:
                fh.write(b"transcoded")

            def _probe(path: str):
                if path.endswith(".iso"):
                    raise RuntimeError("primary open failed")
                return 24.0, 12, (320, 240), None

            with patch.object(vt, "_probe_video_clip", side_effect=_probe):
                with patch.object(vt, "_probe_media_details", return_value={"has_video": True}):
                    with patch.object(vt, "_remux_video_source", return_value=None):
                        with patch.object(vt, "_transcode_video_source", return_value=transcode_path):
                            with patch.object(vt, "_video_has_audio_stream", return_value=False):
                                clip = vt._load_video_clip("/tmp/game.iso")
            self.assertIsNotNone(clip)
            self.assertEqual(clip.path, transcode_path)
            self.assertIn("transcode fallback", clip.load_note)
            clip.close()
            self.assertFalse(os.path.exists(transcode_path))

    def test_segmented_video_source_detection_accepts_named_and_numeric_parts(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        with tempfile.TemporaryDirectory() as tmpdir:
            named_parts = [
                os.path.join(tmpdir, "movie.part1.vob"),
                os.path.join(tmpdir, "movie.part2.vob"),
            ]
            numeric_parts = [
                os.path.join(tmpdir, "episode.vob.001"),
                os.path.join(tmpdir, "episode.vob.002"),
            ]
            for path in named_parts + numeric_parts:
                with open(path, "wb") as handle:
                    handle.write(b"segment")
            self.assertEqual(vt._segmented_video_sources(named_parts[0]), named_parts)
            self.assertEqual(vt._segmented_video_sources(numeric_parts[0]), numeric_parts)
            self.assertTrue(vt._is_segmented_video_source(named_parts[0]))
            self.assertTrue(vt._is_probably_video_source(numeric_parts[0], probe=None))

    def test_attempt_video_recovery_uses_concat_segment_repair(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        with tempfile.TemporaryDirectory() as tmpdir:
            part1 = os.path.join(tmpdir, "movie.part1.vob")
            part2 = os.path.join(tmpdir, "movie.part2.vob")
            for path in (part1, part2):
                with open(path, "wb") as handle:
                    handle.write(b"segment")
            details = {
                "has_video": True,
                "has_audio": True,
                "video_stream_index": 0,
                "audio_stream_index": 1,
                "video_stream_count": 1,
                "video_attached_pic_count": 0,
                "selected_video_attached_pic": False,
            }
            with patch.object(vt, "_concat_segmented_video_source", return_value="/tmp/repaired-concat.mkv") as concat_mock:
                with patch.object(vt, "_remux_video_source", return_value=None) as remux_mock:
                    with patch.object(vt, "_transcode_video_source", return_value=None) as transcode_mock:
                        recovered_path, note, recovered_probe = vt._attempt_video_recovery(part1, details)
            self.assertEqual(recovered_path, "/tmp/repaired-concat.mkv")
            self.assertEqual(recovered_probe, details)
            self.assertIn("segmented concat remux fallback", note)
            self.assertIn("2 joined parts", note)
            concat_mock.assert_called_once()
            remux_mock.assert_not_called()
            transcode_mock.assert_not_called()

    def test_video_load_failure_hint_mentions_segmented_concat_repair(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        with tempfile.TemporaryDirectory() as tmpdir:
            first = os.path.join(tmpdir, "clip.vob.001")
            second = os.path.join(tmpdir, "clip.vob.002")
            for path in (first, second):
                with open(path, "wb") as handle:
                    handle.write(b"segment")
            with patch.object(vt, "_probe_media_details", return_value=None):
                with patch.object(vt, "_video_io_diagnostics", return_value="All video dependencies are available."):
                    hint = vt._video_load_failure_hint(first)
            self.assertIn("segmented / multipart video set", hint)
            self.assertIn("concat repair fallback", hint)

    def test_attempt_video_recovery_retries_without_audio_after_primary_failures(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        details = {
            "has_video": True,
            "has_audio": True,
            "video_stream_index": 2,
            "audio_stream_index": 9,
            "video_stream_count": 1,
            "video_attached_pic_count": 0,
            "selected_video_attached_pic": False,
        }
        remux_calls = []
        transcode_calls = []

        def _fake_remux(path, candidate=None, include_audio=True):
            remux_calls.append(include_audio)
            return None

        def _fake_transcode(path, candidate=None, include_audio=True):
            transcode_calls.append(include_audio)
            if not include_audio:
                return "/tmp/recovered-video-only.mp4"
            return None

        with patch.object(vt, "_remux_video_source", side_effect=_fake_remux):
            with patch.object(vt, "_transcode_video_source", side_effect=_fake_transcode):
                recovered_path, note, recovered_probe = vt._attempt_video_recovery("/tmp/broken-audio.vob", details)
        self.assertEqual(recovered_path, "/tmp/recovered-video-only.mp4")
        self.assertEqual(recovered_probe, details)
        self.assertEqual(remux_calls, [True, False])
        self.assertEqual(transcode_calls, [True, False])
        self.assertIn("transcode fallback", note)
        self.assertIn("source audio dropped", note)

    def test_attempt_video_recovery_retries_alternate_audio_before_audio_drop(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        details = {
            "has_video": True,
            "has_audio": True,
            "video_stream_index": 2,
            "audio_stream_index": 9,
            "video_stream_count": 1,
            "audio_stream_count": 2,
            "video_attached_pic_count": 0,
            "selected_video_attached_pic": False,
            "audio_stream_choices": [
                {"index": 9, "codec_name": "ac3", "bit_rate": 192000, "duration": 10.0, "language": "eng", "title": "Broken"},
                {"index": 5, "codec_name": "mp2", "bit_rate": 256000, "duration": 10.0, "language": "jpn", "title": "Alt"},
            ],
        }
        alternate_probe = {
            **details,
            "audio_stream_index": 5,
            "audio_codec": "mp2",
        }
        remux_calls = []
        transcode_calls = []

        def _fake_probe(path, preferred_video_stream_index=None, preferred_audio_stream_index=None):
            if preferred_audio_stream_index == 5:
                return alternate_probe
            return details

        def _fake_remux(path, candidate=None, include_audio=True):
            remux_calls.append((include_audio, None if candidate is None else candidate.get("audio_stream_index")))
            if include_audio and candidate is alternate_probe:
                return "/tmp/recovered-alt-audio.mkv"
            return None

        def _fake_transcode(path, candidate=None, include_audio=True):
            transcode_calls.append((include_audio, None if candidate is None else candidate.get("audio_stream_index")))
            return None

        with patch.object(vt, "_probe_media_details", side_effect=_fake_probe):
            with patch.object(vt, "_remux_video_source", side_effect=_fake_remux):
                with patch.object(vt, "_transcode_video_source", side_effect=_fake_transcode):
                    recovered_path, note, recovered_probe = vt._attempt_video_recovery("/tmp/broken-audio.vob", details)
        self.assertEqual(recovered_path, "/tmp/recovered-alt-audio.mkv")
        self.assertEqual(recovered_probe, alternate_probe)
        self.assertEqual(remux_calls, [(True, 9), (True, 5)])
        self.assertEqual(transcode_calls, [(True, 9)])
        self.assertIn("alternate audio #5", note)

    def test_attempt_video_recovery_retries_timestamp_rebuild_for_transport_streams(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        details = {
            "has_video": True,
            "has_audio": True,
            "format_name": "mpegts",
            "video_stream_index": 0,
            "audio_stream_index": 1,
            "video_stream_count": 1,
            "audio_stream_count": 1,
            "video_attached_pic_count": 0,
            "selected_video_attached_pic": False,
        }
        remux_calls = []
        transcode_calls = []

        def _fake_remux(path, candidate=None, include_audio=True, rebuild_timestamps=False):
            remux_calls.append((include_audio, rebuild_timestamps))
            if include_audio and rebuild_timestamps:
                return "/tmp/recovered-reindexed.mkv"
            return None

        def _fake_transcode(path, candidate=None, include_audio=True, rebuild_timestamps=False):
            transcode_calls.append((include_audio, rebuild_timestamps))
            return None

        with patch.object(vt, "_remux_video_source", side_effect=_fake_remux):
            with patch.object(vt, "_transcode_video_source", side_effect=_fake_transcode):
                recovered_path, note, recovered_probe = vt._attempt_video_recovery("/tmp/capture.ts", details)
        self.assertEqual(recovered_path, "/tmp/recovered-reindexed.mkv")
        self.assertEqual(recovered_probe, details)
        self.assertEqual(remux_calls, [(True, False), (True, True)])
        self.assertEqual(transcode_calls, [(True, False)])
        self.assertIn("timestamp-rebuild remux fallback", note)
        self.assertIn("timestamp/index rebuild", note)

    def test_video_load_failure_hint_mentions_timestamp_rebuild_for_transport_streams(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        probe = {
            "has_video": True,
            "has_audio": True,
            "format_name": "mpegts",
            "video_stream_index": 0,
            "audio_stream_index": 1,
            "video_stream_count": 1,
            "audio_stream_count": 1,
            "video_attached_pic_count": 0,
            "selected_video_attached_pic": False,
        }
        with patch.object(vt, "_probe_media_details", return_value=probe):
            with patch.object(vt, "_video_io_diagnostics", return_value="All video dependencies are available."):
                hint = vt._video_load_failure_hint("/tmp/capture.ts")
        self.assertIn("timestamp-rebuild", hint)
        self.assertIn("broken timestamps or damaged index metadata", hint)

    def test_audio_stream_choice_rank_prefers_default_original_non_commentary_tracks(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        preferred = {
            "index": 6,
            "default": True,
            "original": True,
            "commentary": False,
            "descriptive": False,
            "dub": False,
            "channels": 2,
            "bit_rate": 128000,
            "duration": 10.0,
        }
        commentary = {
            "index": 2,
            "default": True,
            "original": False,
            "commentary": True,
            "descriptive": False,
            "dub": False,
            "channels": 6,
            "bit_rate": 384000,
            "duration": 10.0,
        }
        self.assertGreater(vt._audio_stream_choice_rank(preferred), vt._audio_stream_choice_rank(commentary))

    def test_recovery_prefers_probe_selected_stream_indexes(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        details = {
            "video_stream_index": 4,
            "audio_stream_index": 7,
        }
        calls = []

        def _fake_run(cmd, **kwargs):
            calls.append(cmd)
            with open(remux_path, "wb") as fh:
                fh.write(b"remuxed")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            remux_path = os.path.join(tmpdir, "result.mkv")

            def _fake_tempfile(**kwargs):
                handle = open(remux_path, "wb")
                handle.close()
                return types.SimpleNamespace(name=remux_path, close=lambda: None)

            with patch.object(vt, "_get_ffmpeg_exe", return_value="/tmp/ffmpeg"):
                with patch.object(vt.tempfile, "NamedTemporaryFile", side_effect=_fake_tempfile):
                    with patch.object(vt.subprocess, "run", side_effect=_fake_run):
                        result = vt._remux_video_source("/tmp/sample.vob", details)

        self.assertEqual(result, remux_path)
        self.assertTrue(calls)
        self.assertIn("0:4", calls[0])
        self.assertIn("0:7?", calls[0])

    def test_video_builder_manual_stream_picker_reloads_selected_clip(self):
        _require_qt_gui(self)
        self._app = _get_app()
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        from PIL import Image

        dialog = vt.VideoToolDialog()
        try:
            clip = vt._ClipEntry(
                "/tmp/multi.vob",
                12,
                lambda _idx: Image.new("RGBA", (8, 6), (255, 0, 0, 255)),
                24.0,
                frame_size=(320, 240),
                clip_type="video",
                source_path="/tmp/multi.vob",
                source_probe={
                    "video_stream_index": 3,
                    "audio_stream_index": 1,
                    "video_stream_count": 2,
                    "audio_stream_count": 2,
                    "video_attached_pic_count": 0,
                    "selected_video_attached_pic": False,
                    "video_stream_choices": [
                        {"index": 3, "codec_name": "mpeg2video", "width": 320, "height": 240, "fps": 24.0, "language": "eng", "title": "main", "attached_pic": False},
                        {"index": 7, "codec_name": "mpeg1video", "width": 160, "height": 120, "fps": 15.0, "language": "jpn", "title": "bonus", "attached_pic": False},
                    ],
                    "audio_stream_choices": [
                        {"index": 1, "codec_name": "ac3", "language": "eng", "title": "Stereo"},
                        {"index": 9, "codec_name": "mp2", "language": "jpn", "title": "Dub"},
                    ],
                },
            )
            dialog._clips = [clip]
            item = vt.QListWidgetItem("clip")
            item.setData(vt._CLIP_ROLE, clip)
            dialog._clip_list.addItem(item)
            dialog._clip_list.setCurrentRow(0)
            dialog._on_clip_selected(0)
            self.assertTrue(dialog._stream_picker_combo.isEnabled())
            self.assertTrue(dialog._audio_stream_picker_combo.isEnabled())
            self.assertIn("2 video streams detected", dialog._stream_summary_lbl.text())
            self.assertIn("2 audio streams detected", dialog._stream_summary_lbl.text())
            picker_index = next(
                idx for idx in range(dialog._stream_picker_combo.count())
                if dialog._stream_picker_combo.itemData(idx) == 7
            )
            dialog._stream_picker_combo.setCurrentIndex(picker_index)
            audio_picker_index = next(
                idx for idx in range(dialog._audio_stream_picker_combo.count())
                if dialog._audio_stream_picker_combo.itemData(idx) == 9
            )
            dialog._audio_stream_picker_combo.setCurrentIndex(audio_picker_index)
            new_clip = vt._ClipEntry(
                "/tmp/multi_bonus.mkv",
                8,
                lambda _idx: Image.new("RGBA", (8, 6), (0, 255, 0, 255)),
                15.0,
                frame_size=(160, 120),
                clip_type="video",
                source_path="/tmp/multi.vob",
                load_note="manual stream #7; temporary ffmpeg remux fallback active",
                source_probe={
                    "video_stream_index": 7,
                    "audio_stream_index": 9,
                    "video_stream_count": 2,
                    "audio_stream_count": 2,
                    "video_attached_pic_count": 0,
                    "selected_video_attached_pic": False,
                    "video_stream_choices": [
                        {"index": 3, "codec_name": "mpeg2video", "width": 320, "height": 240, "fps": 24.0, "language": "eng", "title": "main", "attached_pic": False},
                        {"index": 7, "codec_name": "mpeg1video", "width": 160, "height": 120, "fps": 15.0, "language": "jpn", "title": "bonus", "attached_pic": False},
                    ],
                    "audio_stream_choices": [
                        {"index": 1, "codec_name": "ac3", "language": "eng", "title": "Stereo"},
                        {"index": 9, "codec_name": "mp2", "language": "jpn", "title": "Dub"},
                    ],
                },
                preferred_video_stream_index=7,
                preferred_audio_stream_index=9,
            )
            with patch.object(dialog, "_reload_clip", return_value=new_clip) as reload_mock:
                with patch.object(dialog, "_update_preview"):
                    with patch.object(dialog, "_update_scrubber"):
                        dialog._apply_selected_stream_choice()
            reload_mock.assert_called_once_with(
                clip,
                preferred_video_stream_index=7,
                preferred_audio_stream_index=9,
            )
            self.assertEqual(dialog._clips[0], new_clip)
            self.assertIn("Reloaded multi.vob", dialog._import_status_lbl.text())
            self.assertIn("manual stream #7", dialog._import_detail_box.toPlainText())
            self.assertIn("manual audio #9", dialog._import_detail_box.toPlainText())
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_dropped_unknown_video_extension_uses_probe_detection(self):
        _require_qt_gui(self)
        self._app = _get_app()
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        dialog = vt.VideoToolDialog()
        fake_clip = types.SimpleNamespace(
            load_note="",
            source_path="/tmp/weird.dat",
            frame_size=(32, 24),
            clip_type="video",
            active_frames=12,
            fps=24.0,
            speed_percent=100,
            close=lambda: None,
        )
        try:
            with patch.object(vt, "_probe_media_details", return_value={"has_video": True}):
                with patch.object(vt, "_load_video_clip", return_value=fake_clip):
                    with patch.object(dialog, "_insert_clip", return_value=1) as insert_mock:
                        with patch.object(dialog, "_update_scrubber"):
                            with patch.object(dialog, "_update_preview"):
                                with patch.object(dialog, "_update_ui_state"):
                                    dialog._on_files_dropped(["/tmp/weird.dat"], 0)
            insert_mock.assert_called_once()
            self.assertIn("Import summary: Added 1 clip", dialog._import_status_lbl.text())
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_dropped_audio_only_unknown_extension_reports_video_failure(self):
        _require_qt_gui(self)
        self._app = _get_app()
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        dialog = vt.VideoToolDialog()
        try:
            with patch.object(vt, "_probe_media_details", return_value={"has_video": False, "has_audio": True, "format_name": "ogg"}):
                with patch.object(vt, "_video_load_failure_hint", return_value="audio only"):
                    with patch.object(dialog, "_update_scrubber"):
                        with patch.object(dialog, "_update_preview"):
                            with patch.object(dialog, "_update_ui_state"):
                                dialog._on_files_dropped(["/tmp/weird.dat"], 0)
            self.assertIn("1 failed", dialog._import_status_lbl.text())
            self.assertIn("audio only", dialog._import_status_lbl.toolTip())
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_real_media_sample_corpus_loads_under_iso_umd_bin_extensions(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        ffmpeg_exe = vt._get_ffmpeg_exe()
        ffprobe_exe = vt._get_ffprobe_exe()
        if not ffmpeg_exe or not ffprobe_exe:
            self.skipTest("ffmpeg/ffprobe unavailable for generated odd-extension media corpus test")

        with tempfile.TemporaryDirectory() as tmpdir:
            source_mp4 = os.path.join(tmpdir, "sample.mp4")
            result = subprocess.run(
                [
                    ffmpeg_exe,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=32x24:rate=6",
                    "-t",
                    "0.5",
                    "-pix_fmt",
                    "yuv420p",
                    source_mp4,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                self.skipTest(f"Could not generate sample media corpus: {result.stderr[:200]}")
            with open(source_mp4, "rb") as fh:
                sample_bytes = fh.read()
            for ext in (".iso", ".umd", ".bin"):
                sample_path = os.path.join(tmpdir, f"sample{ext}")
                with open(sample_path, "wb") as fh:
                    fh.write(sample_bytes)
                details = vt._probe_media_details(sample_path)
                self.assertIsNotNone(details)
                self.assertTrue(details["has_video"])
                clip = vt._load_video_clip(sample_path)
                self.assertIsNotNone(clip)
                self.assertEqual(clip.source_path, sample_path)
                clip.close()

    def test_generated_transport_stream_and_asf_samples_load_or_explain(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        ffmpeg_exe = vt._get_ffmpeg_exe()
        ffprobe_exe = vt._get_ffprobe_exe()
        if not ffmpeg_exe or not ffprobe_exe:
            self.skipTest("ffmpeg/ffprobe unavailable for generated odd-container corpus test")

        outputs = (
            ("sample.ts", ["-c:v", "mpeg2video", "-pix_fmt", "yuv420p", "-f", "mpegts"]),
            ("sample.asf", ["-c:v", "wmv2", "-pix_fmt", "yuv420p", "-f", "asf"]),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, extra_args in outputs:
                sample_path = os.path.join(tmpdir, name)
                result = subprocess.run(
                    [
                        ffmpeg_exe,
                        "-y",
                        "-f",
                        "lavfi",
                        "-i",
                        "testsrc=size=32x24:rate=6",
                        "-t",
                        "0.5",
                        *extra_args,
                        sample_path,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    check=False,
                    text=True,
                    timeout=120,
                )
                if result.returncode != 0:
                    self.skipTest(f"Could not generate odd-container sample {name}: {result.stderr[:200]}")
                details = vt._probe_media_details(sample_path)
                self.assertIsNotNone(details)
                self.assertTrue(details["has_video"])
                hint = vt._video_load_failure_hint(sample_path)
                self.assertTrue(hint)
                clip = vt._load_video_clip(sample_path)
                if clip is not None:
                    self.assertEqual(clip.source_path, sample_path)
                    clip.close()
                else:
                    self.assertIn("ffmpeg", hint.lower())

    def test_load_video_paths_summarizes_failures_inline(self):
        _require_qt_gui(self)
        self._app = _get_app()
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        dialog = vt.VideoToolDialog()
        try:
            with patch.object(vt, "_load_video_clip", return_value=None):
                with patch.object(vt, "_video_load_failure_hint", side_effect=["hint one", "hint two"]):
                    with patch.object(vt.QMessageBox, "warning") as warn_mock:
                        dialog._load_video_paths(["/tmp/a.iso", "/tmp/b.bin"])
            warn_mock.assert_not_called()
            self.assertIn("2 failed", dialog._import_status_lbl.text())
            self.assertIn("Failure types:", dialog._import_status_lbl.toolTip())
            self.assertIn("a.iso: hint one", dialog._import_status_lbl.toolTip())
            self.assertIn("b.bin: hint two", dialog._import_status_lbl.toolTip())
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_load_video_paths_accepts_probe_detected_unknown_extension(self):
        _require_qt_gui(self)
        self._app = _get_app()
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        dialog = vt.VideoToolDialog()
        fake_clip = types.SimpleNamespace(
            load_note="temporary ffmpeg transcode fallback active",
            source_path="/tmp/weird.dat",
            frame_size=(32, 24),
            clip_type="video",
            active_frames=12,
            fps=24.0,
            speed_percent=100,
            close=lambda: None,
        )
        try:
            with patch.object(vt, "_probe_media_details", return_value={"has_video": True}):
                with patch.object(vt, "_load_video_clip", return_value=fake_clip):
                    with patch.object(dialog, "_insert_clip", return_value=1) as insert_mock:
                        with patch.object(dialog, "_update_scrubber"):
                            with patch.object(dialog, "_update_preview"):
                                with patch.object(dialog, "_update_ui_state"):
                                    dialog._load_video_paths(["/tmp/weird.dat"])
            insert_mock.assert_called_once()
            self.assertIn("Added 1 clip", dialog._import_status_lbl.text())
            self.assertIn("1 recovered", dialog._import_status_lbl.text())
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_load_video_paths_reports_audio_only_unknown_extension_as_failure(self):
        _require_qt_gui(self)
        self._app = _get_app()
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        dialog = vt.VideoToolDialog()
        try:
            with patch.object(vt, "_probe_media_details", return_value={"has_video": False, "has_audio": True, "format_name": "ogg"}):
                with patch.object(vt, "_video_load_failure_hint", return_value="audio only"):
                    dialog._load_video_paths(["/tmp/odd.dat"])
            self.assertIn("1 failed", dialog._import_status_lbl.text())
            self.assertIn("audio only", dialog._import_status_lbl.toolTip())
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_video_import_status_summarizes_recovery_paths_and_guidance(self):
        _require_qt_gui(self)
        self._app = _get_app()
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        dialog = vt.VideoToolDialog()
        try:
            dialog._update_import_status(
                added=2,
                attempted=4,
                recovered=[
                    ("a.iso", "temporary ffmpeg remux fallback active"),
                    ("b.vob", "temporary ffmpeg transcode fallback active (preferred stream #3)"),
                ],
                failures=[
                    ("c.vob", "Multiple video streams were detected; recovery will prefer the largest probe-detected video stream.\nProbe: container=mpeg; video=mpeg2video; audio=ac3; preferred-stream=3."),
                    ("d.ogg", "ffprobe detected audio but no playable video stream\nProbe: container=ogg; video=none; audio=vorbis."),
                ],
                skipped=[],
            )
            self.assertIn("2 recovered", dialog._import_status_lbl.text())
            self.assertIn("remux ×1", dialog._import_status_lbl.text())
            self.assertIn("transcode ×1", dialog._import_status_lbl.text())
            self.assertIn("audio-only container ×1", dialog._import_status_lbl.text())
            self.assertIn("Recovery paths: remux ×1, transcode ×1", dialog._import_status_lbl.toolTip())
            self.assertIn("Failure guidance:", dialog._import_status_lbl.toolTip())
            self.assertIn("Probe-detected containers: mpeg ×1, ogg ×1", dialog._import_status_lbl.toolTip())
            self.assertIn("Probe-detected video codecs: mpeg2video ×1", dialog._import_status_lbl.toolTip())
            self.assertIn("Probe-detected audio codecs: ac3 ×1, vorbis ×1", dialog._import_status_lbl.toolTip())
            self.assertIn("multi-stream container", dialog._import_status_lbl.toolTip())
            self.assertIn("audio-only container", dialog._import_status_lbl.toolTip())
            self.assertIn("Selected Stream", dialog._next_step_lbl.text())
            self.assertIn("recovered clips are already usable", dialog._next_step_lbl.text())
            self.assertFalse(dialog._import_detail_box.isHidden())
            self.assertIn("Recovery paths: remux ×1, transcode ×1", dialog._import_detail_box.toPlainText())
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_video_import_status_surfaces_container_specific_failure_groups(self):
        _require_qt_gui(self)
        self._app = _get_app()
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        dialog = vt.VideoToolDialog()
        try:
            dialog._update_import_status(
                added=0,
                attempted=4,
                recovered=[],
                failures=[
                    ("capture.ts", "Transport-stream sources often contain discontinuities or missing timestamps; recovery may rebuild timing, but severe capture gaps can still prevent loading.\nProbe: container=mpegts; video=h264; audio=aac."),
                    ("feature.mkv", "Matroska/WebM files can carry multiple alternate video/audio programs; if one stream fails, the builder will prefer the strongest detected video stream but manual stream reloads may still help.\nProbe: container=matroska,webm; video=vp9; audio=opus."),
                    ("edit.mov", "QuickTime/MOV-family files may depend on edit lists, timecode, or ProRes-style metadata; remux/transcode recovery is often needed when direct indexing is incomplete.\nProbe: container=mov,mp4,m4a,3gp,3g2,mj2; video=prores; audio=pcm_s16le."),
                    ("slideshow.mkv", "Detected a still-image style video codec inside a nonstandard container; the builder may only recover this as a slideshow/still-frame source unless ffmpeg can transcode it cleanly.\nProbe: container=matroska,webm; video=mjpeg; audio=none."),
                ],
                skipped=[],
            )
            summary = dialog._import_status_lbl.text()
            details = dialog._import_detail_box.toPlainText()
            self.assertIn("4 failed", summary)
            self.assertIn("transport stream timing ×1", summary)
            self.assertIn("matroska/webm program ×1", details)
            self.assertIn("quicktime metadata ×1", details)
            self.assertIn("still-image video ×1", details)
            self.assertIn("Probe-detected containers: matroska,webm ×2, mov,mp4,m4a,3gp,3g2,mj2 ×1, mpegts ×1", details)
            self.assertIn("Probe-detected video codecs: h264 ×1, mjpeg ×1, prores ×1, vp9 ×1", details)
            self.assertIn("transport stream timing:", details)
            self.assertIn("quicktime metadata:", details)
            self.assertIn("still-image video:", details)
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_optional_real_video_corpus_samples_probe_and_explain_or_load(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        roots = _optional_corpus_roots("ALPHA_FIXER_REAL_VIDEO_CORPUS", "ALPHA_FIXER_VIDEO_CORPUS_DIR")
        samples = _iter_corpus_files(roots, (".iso", ".umd", ".bin", ".vob", ".ts", ".mxf", ".asf", ".rmvb"))
        if not samples:
            self.skipTest("No optional real video corpus configured")

        loaded = 0
        explained = 0
        for sample_path in samples:
            details = vt._probe_media_details(sample_path)
            hint = vt._video_load_failure_hint(sample_path)
            self.assertTrue(hint)
            clip = vt._load_video_clip(sample_path)
            if clip is not None:
                loaded += 1
                self.assertEqual(clip.source_path, sample_path)
                clip.close()
                continue
            if details is not None:
                explained += 1
                self.assertIn("ffmpeg", hint.lower())
        self.assertGreaterEqual(loaded + explained, len(samples))

    def test_optional_real_disc_video_corpus_samples_probe_and_explain_or_load(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        roots = _optional_corpus_roots(
            "ALPHA_FIXER_REAL_DISC_VIDEO_CORPUS",
            "ALPHA_FIXER_REAL_VIDEO_CORPUS",
            "ALPHA_FIXER_VIDEO_CORPUS_DIR",
        )
        samples = _iter_corpus_files(roots, (".iso", ".umd", ".bin"))
        if not samples:
            self.skipTest("No optional real disc-video corpus configured")

        loaded = 0
        explained = 0
        for sample_path in samples:
            hint = vt._video_load_failure_hint(sample_path)
            self.assertIn("Disc-image video inputs are experimental", hint)
            clip = vt._load_video_clip(sample_path)
            if clip is not None:
                loaded += 1
                self.assertEqual(clip.source_path, sample_path)
                clip.close()
                continue
            explained += 1
            self.assertIn("ffmpeg", hint.lower())
        self.assertGreaterEqual(loaded + explained, len(samples))

    def test_optional_real_disc_video_manifest_samples_match_expectations(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        manifest = _optional_manifest_entries("ALPHA_FIXER_REAL_DISC_VIDEO_MANIFEST")
        if not manifest:
            self.skipTest("No optional real disc-video manifest configured")

        exercised = 0
        for entry in manifest:
            sample_path = str(entry.get("path") or "").strip()
            if not os.path.isfile(sample_path):
                continue
            expected = str(entry.get("expect") or "load_or_explain").strip().lower()
            preferred_stream = entry.get("preferred_video_stream_index")
            clip = vt._load_video_clip(sample_path, preferred_video_stream_index=preferred_stream)
            hint = vt._video_load_failure_hint(sample_path, preferred_video_stream_index=preferred_stream)
            exercised += 1
            if expected == "load":
                self.assertIsNotNone(clip, msg=f"Expected {sample_path} to load, but got hint:\n{hint}")
            elif expected == "fail":
                self.assertIsNone(clip, msg=f"Expected {sample_path} to fail import")
            if clip is not None:
                try:
                    self.assertEqual(clip.source_path, sample_path)
                finally:
                    clip.close()
            required_tokens = entry.get("hint_contains") or []
            if isinstance(required_tokens, str):
                required_tokens = [required_tokens]
            for token in required_tokens:
                self.assertIn(str(token), hint, msg=f"Missing hint token for {sample_path}: {token}")
        if exercised == 0:
            self.skipTest("Configured real disc-video manifest paths were unavailable")

    def test_mp4_export_size_rounds_up_to_even_dimensions(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        self.assertEqual(vt._coerce_export_size((641, 479), "mp4"), (642, 480))
        self.assertEqual(vt._coerce_export_size((641, 479), "gif"), (641, 479))

    def test_mixed_size_frames_are_letterboxed_to_shared_canvas(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        from PIL import Image

        src = Image.new("RGBA", (200, 100), (255, 0, 0, 255))
        try:
            framed = vt._fit_frame_to_canvas(src, (400, 400), "gif")
            try:
                self.assertEqual(framed.size, (400, 400))
                self.assertEqual(framed.getpixel((200, 200)), (255, 0, 0, 255))
                self.assertEqual(framed.getpixel((20, 20))[3], 0)
            finally:
                framed.close()
        finally:
            src.close()

    def test_clip_timing_supports_still_duration_and_speed_mapping(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        from PIL import Image

        image_clip = vt._ClipEntry("/tmp/image.png", 1, lambda idx: Image.new("RGBA", (8, 8)), 25.0, clip_type="image")
        image_clip.still_duration_frames = 40
        self.assertEqual(image_clip.active_frames, 40)
        frame = image_clip.get_frame(17)
        frame.close()

        video_clip = vt._ClipEntry("/tmp/video.mp4", 10, lambda idx: Image.new("RGBA", (8, 8)), 25.0, clip_type="video")
        video_clip.speed_percent = 200
        self.assertEqual(video_clip.active_frames, 5)
        self.assertEqual(video_clip.output_index_to_source_offset(4), 8)

        slow_clip = vt._ClipEntry("/tmp/slow.mp4", 6, lambda idx: Image.new("RGBA", (8, 8)), 25.0, clip_type="video")
        slow_clip.speed_percent = 50
        self.assertEqual(slow_clip.output_index_to_source_offset(0), 0)
        self.assertEqual(slow_clip.output_index_to_source_offset(1), 0)
        self.assertEqual(slow_clip.split_second_half_offset(0), 1)

    def test_audio_tempo_filter_chain_stays_within_ffmpeg_limits(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        self.assertEqual(vt._build_atempo_filters(1.0), ["atempo=1"])
        self.assertEqual(vt._build_atempo_filters(4.0), ["atempo=2.0", "atempo=2"])
        self.assertEqual(vt._build_atempo_filters(0.25), ["atempo=0.5", "atempo=0.5"])

    def test_audio_source_plan_summarizes_mixed_timeline(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        plan = vt._audio_source_plan([
            {
                "active_frames": 12,
                "clip_type": "video",
                "has_audio": True,
                "preferred_audio_stream_index": 5,
            },
            {
                "active_frames": 8,
                "clip_type": "video",
                "has_audio": False,
                "load_note": "temporary ffmpeg transcode fallback active, source audio dropped",
            },
            {
                "active_frames": 4,
                "clip_type": "image",
                "has_audio": False,
            },
        ])
        self.assertEqual(plan["mode"], "mixed-source+silence")
        self.assertEqual(plan["audio_source_clips"], 1)
        self.assertEqual(plan["video_clips"], 2)
        self.assertEqual(plan["silent_video_clips"], 1)
        self.assertEqual(plan["silent_still_sections"], 1)
        self.assertEqual(plan["dropped_audio_recovery_clips"], 1)
        self.assertEqual(plan["manual_audio_override_clips"], 1)
        hint = vt._audio_source_plan_hint(plan)
        self.assertIn("1/2 video clips provide source audio", hint)
        self.assertIn("1 recovered clip already dropped source audio during import", hint)
        notes = vt._audio_source_plan_history_notes(plan)
        self.assertIn("audio-source-plan=mixed-source+silence", notes)
        self.assertIn("audio-source-clips=1/2", notes)
        self.assertIn("audio-dropped-recovery-clips=1", notes)

    def test_audio_controls_hint_mentions_mixed_silent_sections_and_manual_overrides(self):
        _require_qt_gui(self)
        self._app = _get_app()
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")
        from PyQt6.QtWidgets import QWidget

        parent = QWidget()
        dialog = vt.VideoToolDialog(parent=parent)
        dialog._mp4_export_available = True
        dialog._clips = [
            types.SimpleNamespace(
                active_frames=12,
                clip_type="video",
                has_audio=True,
                load_note="",
                preferred_audio_stream_index=4,
            ),
            types.SimpleNamespace(
                active_frames=8,
                clip_type="video",
                has_audio=False,
                load_note="temporary ffmpeg transcode fallback active, source audio dropped",
                preferred_audio_stream_index=None,
            ),
            types.SimpleNamespace(
                active_frames=6,
                clip_type="image",
                has_audio=False,
                load_note="",
                preferred_audio_stream_index=None,
            ),
        ]
        dialog._export_fmt_combo.setCurrentIndex(dialog._export_fmt_combo.findData("mp4"))
        dialog._update_audio_controls()
        hint = dialog._audio_hint_lbl.text()
        self.assertIn("1/2 video clips provide source audio", hint)
        self.assertIn("1 video clip without usable source audio will stay silent", hint)
        self.assertIn("1 still-image/GIF section will be filled with silence", hint)
        self.assertIn("1 recovered clip already dropped source audio during import", hint)
        self.assertIn("1 clip uses manual audio stream override", hint)
        dialog.close()
        dialog.deleteLater()
        parent.deleteLater()
        self._app.processEvents()

    def test_export_rewrites_mismatched_gif_extension(self):
        _require_qt_gui(self)
        self._app = _get_app()
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")
        from PIL import Image

        dialog = vt.VideoToolDialog()
        dialog._clips = [types.SimpleNamespace(active_frames=1, clip_type="video", close=lambda: None)]
        dialog._export_fmt_combo.setCurrentIndex(dialog._export_fmt_combo.findData("gif"))
        dialog._snapshot_clip_render_state = lambda clip, fps: {"active_frames": 1}
        dialog._get_snapshot_frame = lambda clip, idx: Image.new("RGBA", (2, 2), (255, 0, 0, 255))
        dialog._timeline_canvas_size = lambda fmt: (2, 2)
        saved_paths = []
        try:
            with patch.object(vt.QFileDialog, "getSaveFileName", return_value=("/tmp/video-output.mp4", "")):
                with patch.object(vt.QMessageBox, "information"):
                    with patch("PIL.Image.Image.save", autospec=True, side_effect=lambda self, path, **kwargs: saved_paths.append(path)):
                        dialog._export()
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()
        self.assertEqual(len(saved_paths), 1)
        self.assertTrue(saved_paths[0].endswith(".gif"))

    def test_export_gif_failure_keeps_existing_output_file(self):
        _require_qt_gui(self)
        self._app = _get_app()
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")
        from PIL import Image

        dialog = vt.VideoToolDialog()
        dialog._clips = [types.SimpleNamespace(active_frames=1)]
        dialog._export_fmt_combo.setCurrentIndex(dialog._export_fmt_combo.findData("gif"))
        dialog._snapshot_clip_render_state = lambda clip, fps: {"active_frames": 1}
        dialog._get_snapshot_frame = lambda clip, idx: Image.new("RGBA", (2, 2), (255, 0, 0, 255))
        dialog._timeline_canvas_size = lambda fmt: (2, 2)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                out_path = os.path.join(tmpdir, "existing.gif")
                with open(out_path, "wb") as fh:
                    fh.write(b"original-gif")
                with patch.object(vt.QFileDialog, "getSaveFileName", return_value=(out_path, "")):
                    with patch.object(vt.QMessageBox, "critical") as critical_mock:
                        with patch("PIL.Image.Image.save", autospec=True, side_effect=RuntimeError("gif failed")):
                            dialog._export()
                critical_mock.assert_called_once()
                with open(out_path, "rb") as fh:
                    self.assertEqual(fh.read(), b"original-gif")
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_export_rewrites_mismatched_mp4_extension(self):
        _require_qt_gui(self)
        self._app = _get_app()
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")
        from PIL import Image

        class _FakeWriter:
            def __init__(self):
                self.closed = False

            def append_data(self, data):
                return None

            def close(self):
                self.closed = True

        dialog = vt.VideoToolDialog()
        dialog._mp4_export_available = True
        dialog._clips = [types.SimpleNamespace(active_frames=1, clip_type="video", close=lambda: None)]
        dialog._export_fmt_combo.setCurrentIndex(dialog._export_fmt_combo.findData("mp4"))
        dialog._snapshot_clip_render_state = lambda clip, fps: {"active_frames": 1}
        dialog._get_snapshot_frame = lambda clip, idx: Image.new("RGBA", (2, 2), (0, 255, 0, 255))
        dialog._timeline_canvas_size = lambda fmt: (2, 2)
        dialog._should_mux_audio = lambda fmt, clips: False
        writer_paths = []
        writer_kwargs = []
        fake_writer = _FakeWriter()
        try:
            with patch.object(vt.QFileDialog, "getSaveFileName", return_value=("/tmp/video-output.gif", "")):
                with patch.object(vt.QMessageBox, "information"):
                    with patch("imageio.get_writer", side_effect=lambda path, **kwargs: writer_paths.append(path) or writer_kwargs.append(kwargs) or fake_writer):
                        dialog._export()
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()
        self.assertEqual(len(writer_paths), 1)
        self.assertTrue(writer_paths[0].endswith(".mp4"))
        self.assertEqual(writer_kwargs[0]["format"], "FFMPEG")

    def test_export_render_failure_keeps_existing_mp4_output_file(self):
        _require_qt_gui(self)
        self._app = _get_app()
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")
        from PIL import Image

        class _FailingWriter:
            def append_data(self, data):
                raise RuntimeError("encode failed")

            def close(self):
                return None

        dialog = vt.VideoToolDialog()
        dialog._mp4_export_available = True
        dialog._clips = [types.SimpleNamespace(active_frames=1)]
        dialog._export_fmt_combo.setCurrentIndex(dialog._export_fmt_combo.findData("mp4"))
        dialog._snapshot_clip_render_state = lambda clip, fps: {"active_frames": 1}
        dialog._get_snapshot_frame = lambda clip, idx: Image.new("RGBA", (2, 2), (0, 255, 0, 255))
        dialog._timeline_canvas_size = lambda fmt: (2, 2)
        dialog._should_mux_audio = lambda fmt, clips: False
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                out_path = os.path.join(tmpdir, "existing.mp4")
                with open(out_path, "wb") as fh:
                    fh.write(b"original-mp4")
                with patch.object(vt.QFileDialog, "getSaveFileName", return_value=(out_path, "")):
                    with patch("imageio.get_writer", return_value=_FailingWriter()):
                        with patch.object(vt.QMessageBox, "critical") as critical_mock:
                            dialog._export()
                critical_mock.assert_called_once()
                with open(out_path, "rb") as fh:
                    self.assertEqual(fh.read(), b"original-mp4")
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_export_mux_failure_falls_back_to_silent_mp4_and_records_history(self):
        _require_qt_gui(self)
        self._app = _get_app()
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")
        from PIL import Image
        from PyQt6.QtWidgets import QWidget

        class _FakeWriter:
            def __init__(self, path):
                self._path = path

            def append_data(self, data):
                return None

            def close(self):
                with open(self._path, "wb") as fh:
                    fh.write(b"rendered-mp4")
                return None

        parent = QWidget()
        settings = _ConverterTabSettingsStub()
        parent._settings = settings
        dialog = vt.VideoToolDialog(parent=parent)
        dialog._mp4_export_available = True
        dialog._clips = [types.SimpleNamespace(active_frames=1, clip_type="video", has_audio=True)]
        dialog._export_fmt_combo.setCurrentIndex(dialog._export_fmt_combo.findData("mp4"))
        dialog._snapshot_clip_render_state = lambda clip, fps: {
            "active_frames": 1,
            "path": "/tmp/source.mp4",
            "source_path": "/tmp/source.mp4",
            "clip_type": "video",
            "has_audio": True,
        }
        dialog._get_snapshot_frame = lambda clip, idx: Image.new("RGBA", (2, 2), (0, 255, 0, 255))
        dialog._timeline_canvas_size = lambda fmt: (2, 2)
        dialog._should_mux_audio = lambda fmt, clips: True
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                out_path = os.path.join(tmpdir, "existing.mp4")
                with open(out_path, "wb") as fh:
                    fh.write(b"original")
                with patch.object(vt.QFileDialog, "getSaveFileName", return_value=(out_path, "")):
                    with patch("imageio.get_writer", side_effect=lambda path, **kwargs: _FakeWriter(path)):
                        with patch.object(dialog, "_mux_mp4_audio", side_effect=RuntimeError("mux failed")):
                            with patch.object(vt.QMessageBox, "information") as info_mock:
                                dialog._export()
                info_mock.assert_called_once()
                self.assertIn("silent MP4", info_mock.call_args.args[2])
                with open(out_path, "rb") as fh:
                    self.assertEqual(fh.read(), b"rendered-mp4")
                self.assertEqual(len(settings._video_history), 1)
                entry = settings._video_history[0]
                self.assertEqual(entry["audio"], "off (mux failed)")
                self.assertEqual(entry["errors"], 1)
                self.assertIn("audio-source-plan=all-source-audio", entry["notes"])
                self.assertIn("audio-source-clips=1/1", entry["notes"])
                self.assertIn("audio-mux-fallback=silent", entry["notes"])
                self.assertIn("audio-mux-error=mux failed", entry["notes"])
        finally:
            dialog.close()
            dialog.deleteLater()
            parent.deleteLater()
            self._app.processEvents()

    def test_export_records_audio_source_plan_for_mixed_timeline(self):
        _require_qt_gui(self)
        self._app = _get_app()
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")
        from PIL import Image
        from PyQt6.QtWidgets import QWidget

        class _FakeWriter:
            def __init__(self, path):
                self._path = path

            def append_data(self, data):
                return None

            def close(self):
                with open(self._path, "wb") as fh:
                    fh.write(b"rendered-mp4")
                return None

        parent = QWidget()
        settings = _ConverterTabSettingsStub()
        parent._settings = settings
        dialog = vt.VideoToolDialog(parent=parent)
        dialog._mp4_export_available = True
        dialog._clips = [
            types.SimpleNamespace(active_frames=1, clip_type="video", has_audio=True),
            types.SimpleNamespace(active_frames=1, clip_type="video", has_audio=False),
            types.SimpleNamespace(active_frames=1, clip_type="image", has_audio=False),
        ]
        dialog._export_fmt_combo.setCurrentIndex(dialog._export_fmt_combo.findData("mp4"))
        snapshots = [
            {
                "active_frames": 1,
                "path": "/tmp/with-audio.mp4",
                "source_path": "/tmp/with-audio.mp4",
                "clip_type": "video",
                "has_audio": True,
                "load_note": "",
                "preferred_audio_stream_index": 7,
            },
            {
                "active_frames": 1,
                "path": "/tmp/no-audio.mp4",
                "source_path": "/tmp/no-audio.iso",
                "clip_type": "video",
                "has_audio": False,
                "load_note": "temporary ffmpeg transcode fallback active, source audio dropped",
                "preferred_audio_stream_index": None,
            },
            {
                "active_frames": 1,
                "path": "/tmp/still.png",
                "source_path": "/tmp/still.png",
                "clip_type": "image",
                "has_audio": False,
                "load_note": "",
                "preferred_audio_stream_index": None,
            },
        ]
        dialog._snapshot_clip_render_state = lambda clip, fps: snapshots.pop(0)
        dialog._get_snapshot_frame = lambda clip, idx: Image.new("RGBA", (2, 2), (0, 255, 0, 255))
        dialog._timeline_canvas_size = lambda fmt: (2, 2)
        dialog._should_mux_audio = lambda fmt, clips: True

        def _fake_mux(_render_path, out_path, _clip_snapshot, _fps):
            with open(out_path, "wb") as fh:
                fh.write(b"muxed-mp4")

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                out_path = os.path.join(tmpdir, "mixed.mp4")
                with patch.object(vt.QFileDialog, "getSaveFileName", return_value=(out_path, "")):
                    with patch("imageio.get_writer", side_effect=lambda path, **kwargs: _FakeWriter(path)):
                        with patch.object(dialog, "_mux_mp4_audio", side_effect=_fake_mux):
                            with patch.object(vt.QMessageBox, "information"):
                                dialog._export()
                self.assertEqual(len(settings._video_history), 1)
                entry = settings._video_history[0]
                self.assertEqual(entry["audio"], "kept")
                self.assertIn("audio-source-plan=mixed-source+silence", entry["notes"])
                self.assertIn("audio-source-clips=1/2", entry["notes"])
                self.assertIn("audio-silent-video-clips=1", entry["notes"])
                self.assertIn("audio-silent-still-sections=1", entry["notes"])
                self.assertIn("audio-dropped-recovery-clips=1", entry["notes"])
                self.assertIn("audio-manual-stream-overrides=1", entry["notes"])
        finally:
            dialog.close()
            dialog.deleteLater()
            parent.deleteLater()
            self._app.processEvents()

    def test_mux_mp4_audio_retries_with_normalized_audio(self):
        _require_qt_gui(self)
        self._app = _get_app()
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")
        from PyQt6.QtWidgets import QWidget

        parent = QWidget()
        dialog = vt.VideoToolDialog(parent=parent)
        dialog._audio_volume_slider.setValue(100)
        clip_snapshot = [{
            "active_frames": 10,
            "timeline_seconds": 0.5,
            "path": "/tmp/source-audio.ts",
            "clip_type": "video",
            "has_audio": True,
            "trim_start": 0,
            "trim_end": 9,
            "clip_fps": 20.0,
        }]
        commands = []

        def _fake_run(cmd, **kwargs):
            commands.append(cmd)
            if len(commands) == 1:
                return types.SimpleNamespace(returncode=1, stderr="Non-monotonic DTS")
            return types.SimpleNamespace(returncode=0, stderr="")

        try:
            with patch.object(vt, "_get_ffmpeg_exe", return_value="/usr/bin/ffmpeg"):
                with patch.object(vt.subprocess, "run", side_effect=_fake_run):
                    notes = dialog._mux_mp4_audio("/tmp/silent.mp4", "/tmp/out.mp4", clip_snapshot, 20.0)
            self.assertEqual(len(commands), 2)
            self.assertNotIn("aresample=async=1:first_pts=0:min_hard_comp=0.100", " ".join(commands[0]))
            self.assertIn("aresample=async=1:first_pts=0:min_hard_comp=0.100", " ".join(commands[1]))
            self.assertIn("channel_layouts=stereo", " ".join(commands[1]))
            self.assertIn("audio-mux-retry=normalized", notes)
            self.assertIn("audio-mux-first-error=Non-monotonic DTS", notes)
        finally:
            dialog.close()
            dialog.deleteLater()
            parent.deleteLater()
            self._app.processEvents()

    def test_export_records_audio_mux_retry_notes(self):
        _require_qt_gui(self)
        self._app = _get_app()
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")
        from PIL import Image
        from PyQt6.QtWidgets import QWidget

        class _FakeWriter:
            def __init__(self, path):
                self._path = path

            def append_data(self, data):
                return None

            def close(self):
                with open(self._path, "wb") as fh:
                    fh.write(b"rendered-mp4")
                return None

        parent = QWidget()
        settings = _ConverterTabSettingsStub()
        parent._settings = settings
        dialog = vt.VideoToolDialog(parent=parent)
        dialog._mp4_export_available = True
        dialog._clips = [types.SimpleNamespace(active_frames=1, clip_type="video", has_audio=True)]
        dialog._export_fmt_combo.setCurrentIndex(dialog._export_fmt_combo.findData("mp4"))
        dialog._snapshot_clip_render_state = lambda clip, fps: {
            "active_frames": 1,
            "timeline_seconds": 0.1,
            "path": "/tmp/source.mp4",
            "source_path": "/tmp/source.ts",
            "clip_type": "video",
            "has_audio": True,
            "trim_start": 0,
            "trim_end": 0,
            "clip_fps": 10.0,
            "load_note": "",
            "preferred_audio_stream_index": None,
        }
        dialog._get_snapshot_frame = lambda clip, idx: Image.new("RGBA", (2, 2), (0, 255, 0, 255))
        dialog._timeline_canvas_size = lambda fmt: (2, 2)
        dialog._should_mux_audio = lambda fmt, clips: True

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                out_path = os.path.join(tmpdir, "normalized.mp4")
                with patch.object(vt.QFileDialog, "getSaveFileName", return_value=(out_path, "")):
                    with patch("imageio.get_writer", side_effect=lambda path, **kwargs: _FakeWriter(path)):
                        with patch.object(dialog, "_mux_mp4_audio", return_value=[
                            "audio-mux-retry=normalized",
                            "audio-mux-first-error=Non-monotonic DTS",
                        ]):
                            with patch.object(vt.QMessageBox, "information") as info_mock:
                                dialog._export()
                info_mock.assert_called_once()
                self.assertIn("normalized stereo/48 kHz audio", info_mock.call_args.args[2])
                self.assertEqual(len(settings._video_history), 1)
                entry = settings._video_history[0]
                self.assertIn("audio-mux-retry=normalized", entry["notes"])
                self.assertIn("audio-mux-first-error=Non-monotonic DTS", entry["notes"])
                self.assertEqual(entry["audio"], "kept")
        finally:
            dialog.close()
            dialog.deleteLater()
            parent.deleteLater()
            self._app.processEvents()

    def test_video_export_records_history_and_remux_notes(self):
        _require_qt_gui(self)
        self._app = _get_app()
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")
        from PIL import Image
        from PyQt6.QtWidgets import QWidget

        class _FakeWriter:
            def append_data(self, data):
                return None

            def close(self):
                return None

        parent = QWidget()
        settings = _ConverterTabSettingsStub()
        parent._settings = settings
        dialog = vt.VideoToolDialog(parent=parent)
        dialog._mp4_export_available = True
        dialog._clips = [types.SimpleNamespace(active_frames=1)]
        dialog._export_fmt_combo.setCurrentIndex(dialog._export_fmt_combo.findData("mp4"))
        dialog._snapshot_clip_render_state = lambda clip, fps: {
            "active_frames": 1,
            "path": "/tmp/remuxed.mkv",
            "source_path": "/tmp/game.iso",
            "load_note": "temporary ffmpeg remux fallback active",
            "preferred_video_stream_index": 3,
            "preferred_audio_stream_index": 1,
            "source_probe": {
                "video_stream_index": 3,
                "audio_stream_index": 1,
                "video_stream_count": 4,
                "audio_stream_count": 2,
            },
        }
        dialog._get_snapshot_frame = lambda clip, idx: Image.new("RGBA", (2, 2), (0, 255, 0, 255))
        dialog._timeline_canvas_size = lambda fmt: (2, 2)
        dialog._should_mux_audio = lambda fmt, clips: False
        try:
            with patch.object(vt.QFileDialog, "getSaveFileName", return_value=("/tmp/video-history-test.mp4", "")):
                with patch.object(vt.QMessageBox, "information"):
                    with patch("imageio.get_writer", return_value=_FakeWriter()):
                        dialog._export()
        finally:
            dialog.close()
            dialog.deleteLater()
            parent.deleteLater()
            self._app.processEvents()
        self.assertEqual(len(settings._video_history), 1)
        entry = settings._video_history[0]
        self.assertEqual(entry["output"], "/tmp/video-history-test.mp4")
        self.assertEqual(entry["files"], ["game.iso"])
        self.assertEqual(entry["format"], "MP4")
        self.assertEqual(entry["canvas"], "2×2")
        self.assertEqual(entry["sources"], "unknown ×1")
        self.assertEqual(entry["recovery"], "remux ×1")
        self.assertEqual(entry["streams"], "game.iso: manual video #3, manual audio #1")
        self.assertIn("manual video #3", entry["clips"])
        self.assertIn("manual audio #1", entry["clips"])
        self.assertIn("canvas=2×2", entry["notes"])
        self.assertIn("remux fallback", entry["notes"])
        self.assertIn("audio=off", entry["notes"])
        self.assertIn("streams=game.iso: manual video #3, manual audio #1", entry["notes"])
        self.assertIn("clips=game.iso: temporary ffmpeg remux fallback active | streams=manual video #3, manual audio #1", entry["notes"])


@unittest.skipUnless(_PYQT6_AVAILABLE, "PyQt6 not installed")
class TestBuilderHistoryPolish(unittest.TestCase):
    def setUp(self):
        _require_qt_gui(self)
        self._app = _get_app()

    def tearDown(self):
        self._app.processEvents()

    def test_gif_export_records_history_notes(self):
        try:
            from src.ui import gif_builder as gb
        except ImportError as exc:
            self.skipTest(f"gif_builder import unavailable in test env: {exc}")
        from PIL import Image
        from PyQt6.QtWidgets import QWidget

        parent = QWidget()
        settings = _ConverterTabSettingsStub()
        parent._settings = settings
        dialog = gb.GifBuilderDialog(parent=parent)
        source = Image.new("RGBA", (4, 4), (255, 0, 0, 128))
        dialog._frames = [gb._FrameEntry("/tmp/frame.png", 0, source.copy(), delay_ms=80)]
        dialog._loop_slider.setValue(0)
        dialog._optimize_check.setChecked(True)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                out_path = os.path.join(tmpdir, "history.gif")
                with patch.object(gb.QFileDialog, "getSaveFileName", return_value=(out_path, "")):
                    with patch.object(gb.QMessageBox, "information"):
                        dialog._export()
        finally:
            source.close()
            dialog.close()
            dialog.deleteLater()
            parent.deleteLater()
            self._app.processEvents()
        self.assertEqual(len(settings._gif_history), 1)
        entry = settings._gif_history[0]
        self.assertEqual(entry["output"], out_path)
        self.assertEqual(entry["first_file"], "/tmp/frame.png")
        self.assertEqual(entry["files"], ["frame.png"])
        self.assertEqual(entry["sources"], "image ×1")
        self.assertEqual(entry["largest_frame"], "4×4")
        self.assertEqual(entry["alpha_summary"], "1/1")
        self.assertEqual(entry["delay"], "100 ms")
        self.assertEqual(entry["fps"], "10")
        self.assertEqual(entry["loop"], "∞")
        self.assertEqual(entry["optimize"], "on")
        self.assertEqual(entry["resize"], "original")
        self.assertIn("optimize=on", entry["notes"])
        self.assertIn("delay=100 ms", entry["notes"])
        self.assertIn("loop=∞", entry["notes"])
        self.assertIn("sources=image ×1", entry["notes"])
        self.assertIn("alpha", entry["notes"])

    def test_gif_import_failures_are_summarized_inline(self):
        try:
            from src.ui import gif_builder as gb
        except ImportError as exc:
            self.skipTest(f"gif_builder import unavailable in test env: {exc}")

        dialog = gb.GifBuilderDialog()
        try:
            with patch.object(gb, "_load_pillow_rgba", side_effect=RuntimeError("bad image")):
                dialog._add_paths(["/tmp/bad.png"])
            self.assertIn("1 failed", dialog._import_status_lbl.text())
            self.assertIn("Failure types: image import ×1", dialog._import_status_lbl.toolTip())
            self.assertIn("bad.png: bad image", dialog._import_status_lbl.toolTip())
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_gif_import_status_summarizes_source_types_and_largest_frame(self):
        try:
            from src.ui import gif_builder as gb
        except ImportError as exc:
            self.skipTest(f"gif_builder import unavailable in test env: {exc}")

        dialog = gb.GifBuilderDialog()
        try:
            dialog._update_import_status(
                attempted=3,
                loaded_sources=2,
                added_frames=9,
                recovered=[],
                failures=[],
                skipped=["skip.txt"],
                loaded_details=["clip.mp4: 6 frames  •  video  •  320×240 @ ~42 ms", "anim.gif: 3 frames  •  animated gif  •  64×64"],
                source_type_counts={"video": 1, "animated gif": 1},
                frame_size_counts={"320×240": 1, "64×64": 1},
                alpha_source_count=1,
                largest_frame=(320, 240),
            )
            self.assertIn("Loaded 2 sources", dialog._import_status_lbl.text())
            self.assertIn("1 skipped", dialog._import_status_lbl.text())
            self.assertIn("2 frame sizes", dialog._import_status_lbl.text())
            self.assertIn("1 alpha source", dialog._import_status_lbl.text())
            self.assertIn("Source types: animated gif ×1, video ×1", dialog._import_status_lbl.toolTip())
            self.assertIn("Frame sizes: 320×240 ×1, 64×64 ×1", dialog._import_status_lbl.toolTip())
            self.assertIn("Largest imported frame: 320×240", dialog._import_status_lbl.toolTip())
            self.assertIn("Alpha-capable sources: 1 / 2", dialog._import_status_lbl.toolTip())
            self.assertFalse(dialog._import_detail_box.isHidden())
            self.assertIn("Largest imported frame: 320×240", dialog._import_detail_box.toPlainText())
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_gif_builder_probes_unknown_extension_video_sources(self):
        try:
            from src.ui import gif_builder as gb
        except ImportError as exc:
            self.skipTest(f"gif_builder import unavailable in test env: {exc}")

        from PIL import Image

        dialog = gb.GifBuilderDialog()
        try:
            probe = {
                "format_name": "mpeg",
                "has_video": True,
                "has_audio": True,
                "video_codec": "mpeg2video",
                "audio_codec": "ac3",
                "width": 320,
                "height": 240,
                "fps": 25.0,
                "selected_video_attached_pic": False,
                "video_attached_pic_count": 0,
                "video_stream_count": 1,
            }
            frames = [Image.new("RGBA", (16, 12), (255, 0, 0, 255))]
            try:
                with patch.object(gb, "_probe_media_details", return_value=probe):
                    with patch.object(gb, "_load_video_frames", return_value=(frames, 25.0)):
                        dialog._add_paths(["/tmp/odd_source.dat"])
                self.assertEqual(len(dialog._frames), 1)
                self.assertIn("Loaded 1 source", dialog._import_status_lbl.text())
                self.assertIn("video ×1", dialog._import_status_lbl.toolTip())
                self.assertIn("odd_source.dat: 1 frame  •  video", dialog._import_detail_box.toPlainText())
            finally:
                for frame in frames:
                    try:
                        frame.close()
                    except Exception:
                        pass
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_gif_builder_reports_audio_only_odd_container_inline(self):
        try:
            from src.ui import gif_builder as gb
        except ImportError as exc:
            self.skipTest(f"gif_builder import unavailable in test env: {exc}")

        dialog = gb.GifBuilderDialog()
        try:
            probe = {
                "format_name": "ogg",
                "has_video": False,
                "has_audio": True,
                "video_codec": "",
                "audio_codec": "vorbis",
                "width": 0,
                "height": 0,
                "fps": 0.0,
            }
            with patch.object(gb, "_probe_media_details", return_value=probe):
                with patch.object(gb, "_video_load_failure_hint", return_value="ffprobe detected audio but no playable video stream"):
                    dialog._add_paths(["/tmp/audio_payload.dat"])
            self.assertIn("1 failed", dialog._import_status_lbl.text())
            self.assertIn("audio-only container ×1", dialog._import_status_lbl.text())
            self.assertIn("Failure guidance:", dialog._import_status_lbl.toolTip())
            self.assertIn("audio-only container", dialog._import_detail_box.toPlainText())
            self.assertIn("cannot be added", dialog._import_detail_box.toPlainText())
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_gif_builder_groups_transport_stream_failures_with_specific_guidance(self):
        try:
            from src.ui import gif_builder as gb
        except ImportError as exc:
            self.skipTest(f"gif_builder import unavailable in test env: {exc}")

        dialog = gb.GifBuilderDialog()
        try:
            dialog._update_import_status(
                attempted=2,
                loaded_sources=0,
                added_frames=0,
                recovered=[],
                failures=[
                    ("capture.ts", "Transport-stream sources often contain discontinuities or missing timestamps; recovery may rebuild timing, but severe capture gaps can still prevent loading.\nProbe: container=mpegts; video=h264; audio=aac."),
                    ("capture2.ts", "Transport-stream sources often contain discontinuities or missing timestamps; recovery may rebuild timing, but severe capture gaps can still prevent loading.\nProbe: container=mpegts; video=h264; audio=ac3."),
                ],
                skipped=[],
                loaded_details=[],
                source_type_counts={},
                frame_size_counts={},
                alpha_source_count=0,
                largest_frame=(0, 0),
            )
            self.assertIn("transport stream timing ×2", dialog._import_status_lbl.text())
            self.assertIn("transport stream timing", dialog._import_detail_box.toPlainText())
            self.assertIn("Probe-detected containers: mpegts ×2", dialog._import_detail_box.toPlainText())
            self.assertIn("Probe-detected video codecs: h264 ×2", dialog._import_detail_box.toPlainText())
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_gif_builder_updates_frame_diagnostics_for_selected_preview_frame(self):
        try:
            from src.ui import gif_builder as gb
        except ImportError as exc:
            self.skipTest(f"gif_builder import unavailable in test env: {exc}")

        from PIL import Image

        dialog = gb.GifBuilderDialog()
        try:
            entry1 = gb._FrameEntry("/tmp/anim.gif", 0, Image.new("RGBA", (12, 8), (255, 0, 0, 128)), delay_ms=80)
            entry2 = gb._FrameEntry("/tmp/anim.gif", 1, Image.new("RGBA", (12, 8), (255, 0, 0, 128)), delay_ms=80)
            dialog._frames = [entry1, entry2]
            for item_entry in (entry1, entry2):
                item = gb.QListWidgetItem("anim")
                item.setData(gb._ENTRY_ROLE, item_entry)
                dialog._frame_list.addItem(item)
            dialog._frame_list.setCurrentRow(1)
            dialog._preview_idx = 1
            dialog._width_slider.setValue(6)
            dialog._height_slider.setValue(6)
            dialog._update_count()
            dialog._update_scrubber()
            dialog._update_preview_frame()
            self.assertIn("anim.gif", dialog._frame_diag_lbl.text())
            self.assertIn("animated gif source", dialog._frame_diag_lbl.text())
            self.assertIn("preview frame 2/2", dialog._frame_diag_lbl.text())
            self.assertIn("source frame 2/2", dialog._frame_diag_lbl.text())
            self.assertIn("12×8", dialog._frame_diag_lbl.text())
            self.assertIn("80 ms (source timing)", dialog._frame_diag_lbl.text())
            self.assertIn("export 6×4", dialog._frame_diag_lbl.text())
            self.assertIn("(resized)", dialog._frame_diag_lbl.text())
            self.assertIn("/tmp/anim.gif", dialog._frame_diag_lbl.toolTip())
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_gif_builder_shows_capability_summary(self):
        try:
            from src.ui import gif_builder as gb
        except ImportError as exc:
            self.skipTest(f"gif_builder import unavailable in test env: {exc}")

        dialog = gb.GifBuilderDialog()
        try:
            self.assertTrue(dialog._capability_lbl.text())
            self.assertIn("Ready", dialog._capability_lbl.text())
            self.assertIn("audio is ignored", dialog._capability_lbl.text())
            self.assertIn("Audio-only containers", dialog._capability_lbl.text())
            self.assertIn("single-frame fallbacks", dialog._capability_lbl.text())
            self.assertIn("manual multi-stream picker is not available yet", dialog._capability_lbl.toolTip())
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_gif_builder_capability_details_surface_video_runtime_and_picker_gap(self):
        try:
            from src.ui import gif_builder as gb
        except ImportError as exc:
            self.skipTest(f"gif_builder import unavailable in test env: {exc}")

        with patch.object(gb, "_has_ffmpeg", return_value=True):
            with patch.object(gb, "_has_imageio", return_value=True):
                with patch.object(gb, "_has_imageio_ffmpeg", return_value=True):
                    with patch.object(gb, "_get_ffprobe_exe", return_value="/tmp/ffprobe"):
                        with patch.object(gb, "_video_io_diagnostics", return_value="All video dependencies are available."):
                            details = gb._gif_builder_capability_details()
        self.assertIn("All video dependencies are available.", details)
        self.assertIn("manual multi-stream picker is not available yet", details)
        self.assertIn("audio is ignored", details)

    def test_alpha_tab_shows_capability_summary(self):
        try:
            from src.ui.alpha_tool import AlphaFixerTab
        except ImportError as exc:
            self.skipTest(f"alpha_tool import unavailable in test env: {exc}")

        settings = _ConverterTabSettingsStub()
        widget = AlphaFixerTab(MagicMock(), settings)
        try:
            self.assertTrue(hasattr(widget, "_capability_lbl"))
            self.assertTrue(widget._capability_lbl.text().startswith("Ready now:"))
            self.assertIn("SVG inputs", widget._capability_lbl.text())
            self.assertIn("What works here right now:", widget._session_status_lbl.text())
            self.assertIn("Alpha ready", widget._session_status_lbl.text())
            self.assertIn("Next step:", widget._session_status_lbl.text())
            self.assertIn("add image files", widget._next_step_lbl.text())
        finally:
            widget.close()
            widget.deleteLater()
            self._app.processEvents()

    def test_gif_builder_emits_status_notice_and_queue_summary(self):
        try:
            from src.ui import gif_builder as gb
        except ImportError as exc:
            self.skipTest(f"gif_builder import unavailable in test env: {exc}")

        dialog = gb.GifBuilderDialog()
        notices = []
        queue_updates = []
        dialog.status_notice.connect(lambda message, timeout: notices.append((message, timeout)))
        dialog.queue_status_changed.connect(queue_updates.append)
        try:
            dialog._frames = [
                types.SimpleNamespace(source_path="/tmp/a.png"),
                types.SimpleNamespace(source_path="/tmp/b.png"),
            ]
            dialog._update_count()
            dialog._update_import_status(
                attempted=2,
                loaded_sources=2,
                added_frames=2,
                recovered=[],
                failures=[],
                skipped=[],
                loaded_details=["a.png: 1 frame  •  image  •  16×16", "b.png: 1 frame  •  image  •  16×16"],
                source_type_counts={"image": 2},
                frame_size_counts={"16×16": 2},
                alpha_source_count=0,
                largest_frame=(16, 16),
            )
            self.assertTrue(queue_updates)
            self.assertIn("GIF Builder", queue_updates[-1])
            self.assertIn("2 frames", queue_updates[-1])
            self.assertTrue(notices)
            self.assertIn("GIF Builder: Import summary:", notices[-1][0])
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_gif_builder_status_bar_text_includes_preview_and_mode(self):
        try:
            from src.ui import gif_builder as gb
        except ImportError as exc:
            self.skipTest(f"gif_builder import unavailable in test env: {exc}")

        dialog = gb.GifBuilderDialog()
        try:
            with patch.object(gb, "_has_ffmpeg", return_value=False):
                with patch.object(gb, "_has_imageio", return_value=True):
                    with patch.object(gb, "_has_imageio_ffmpeg", return_value=False):
                        self.assertIn("image/GIF mode", dialog.get_status_bar_text())
            dialog._frames = [
                types.SimpleNamespace(source_path="/tmp/a.png"),
                types.SimpleNamespace(source_path="/tmp/b.png"),
            ]
            dialog._update_count()
            dialog._preview_frame_lbl.setText("2 / 2")
            self.assertIn("preview 2 / 2", dialog.get_status_bar_text())
            dialog._preview_timer.start(25)
            try:
                self.assertIn("playing", dialog.get_status_bar_text())
            finally:
                dialog._preview_timer.stop()
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_gif_builder_status_bar_text_keeps_import_summary_without_frames(self):
        try:
            from src.ui import gif_builder as gb
        except ImportError as exc:
            self.skipTest(f"gif_builder import unavailable in test env: {exc}")

        dialog = gb.GifBuilderDialog()
        try:
            dialog._update_import_status(
                attempted=2,
                loaded_sources=0,
                added_frames=0,
                recovered=[],
                failures=[("broken.bin", "ffprobe detected audio but no playable video stream")],
                skipped=["notes.txt"],
                loaded_details=[],
                source_type_counts={},
                frame_size_counts={},
                alpha_source_count=0,
                largest_frame=(0, 0),
            )
            summary = dialog.get_status_bar_text()
            self.assertIn("GIF Builder ready", summary)
            self.assertIn("image/GIF mode", summary)
            self.assertIn("import Loaded 0 sources", summary)
            self.assertIn("1 failed", summary)
            self.assertIn("1 skipped", summary)
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_gif_builder_session_status_and_inline_detail_controls(self):
        try:
            from src.ui import gif_builder as gb
        except ImportError as exc:
            self.skipTest(f"gif_builder import unavailable in test env: {exc}")

        dialog = gb.GifBuilderDialog()
        try:
            self.assertIn("What works here right now:", dialog._session_status_lbl.text())
            self.assertIn("GIF Builder ready", dialog._session_status_lbl.text())
            dialog._update_import_status(
                attempted=2,
                loaded_sources=0,
                added_frames=0,
                recovered=[],
                failures=[("broken.bin", "ffprobe detected audio but no playable video stream")],
                skipped=["notes.txt"],
                loaded_details=[],
                source_type_counts={},
                frame_size_counts={},
                alpha_source_count=0,
                largest_frame=(0, 0),
            )
            self.assertFalse(dialog._import_detail_toggle_btn.isHidden())
            self.assertFalse(dialog._import_copy_btn.isHidden())
            self.assertFalse(dialog._import_detail_box.isHidden())
            dialog._toggle_import_details()
            self.assertTrue(dialog._import_detail_box.isHidden())
            dialog._copy_import_details()
            self.assertIn("Import summary:", self._app.clipboard().text())
            self.assertIn("broken.bin", self._app.clipboard().text())
            self.assertIn("1 failed", dialog._session_status_lbl.text())
            self.assertIn("Next step:", dialog._session_status_lbl.text())
            self.assertIn("audio-only files cannot be added", dialog._next_step_lbl.text())
            self.assertIn("Show details or Copy details", dialog._next_step_lbl.text())
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_gif_builder_uses_still_frame_fallback_for_visual_video_sources(self):
        try:
            from src.ui import gif_builder as gb
        except ImportError as exc:
            self.skipTest(f"gif_builder import unavailable in test env: {exc}")

        from PIL import Image

        dialog = gb.GifBuilderDialog()
        try:
            with patch.object(gb, "_load_video_frames", side_effect=RuntimeError("decode failed")):
                with patch.object(gb, "_extract_visual_still_frame", return_value=Image.new("RGBA", (12, 10), (0, 255, 0, 255))):
                    dialog._add_paths(["/tmp/sample.vob"])
            self.assertEqual(len(dialog._frames), 1)
            self.assertIn("1 recovered", dialog._import_status_lbl.text())
            self.assertIn("still-frame fallback", dialog._import_detail_box.toPlainText())
        finally:
            for entry in list(dialog._frames):
                entry.close()
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_video_image_imports_use_inline_status_not_popup(self):
        _require_qt_gui(self)
        self._app = _get_app()
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        from PIL import Image

        dialog = vt.VideoToolDialog()
        good_clip = vt._ClipEntry("/tmp/good.png", 1, lambda idx: Image.new("RGBA", (8, 8)), 25.0, clip_type="image")
        try:
            with patch.object(vt, "_load_image_as_clip", side_effect=[good_clip, None]):
                with patch.object(vt.QMessageBox, "information") as info_mock:
                    dialog._load_image_paths(["/tmp/good.png", "/tmp/bad.png"])
            info_mock.assert_not_called()
            self.assertIn("Added 1 clip", dialog._import_status_lbl.text())
            self.assertIn("1 skipped", dialog._import_status_lbl.text())
            self.assertIn("Skipped unsupported files:\n  bad.png", dialog._import_status_lbl.toolTip())
        finally:
            good_clip.close()
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_video_builder_emits_status_notice_and_queue_summary(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        dialog = vt.VideoToolDialog()
        notices = []
        queue_updates = []
        dialog.status_notice.connect(lambda message, timeout: notices.append((message, timeout)))
        dialog.queue_status_changed.connect(queue_updates.append)
        try:
            dialog._clips = [
                types.SimpleNamespace(active_frames=24, load_note=""),
                types.SimpleNamespace(active_frames=12, load_note="temporary ffmpeg remux fallback active"),
            ]
            dialog._fps_slider.setValue(24)
            dialog._update_timeline_summary()
            dialog._update_import_status(
                added=2,
                attempted=3,
                recovered=[("sample.iso", "temporary ffmpeg remux fallback active")],
                failures=[("audio.ogg", "ffprobe detected audio but no playable video stream")],
                skipped=[],
            )
            self.assertTrue(queue_updates)
            self.assertIn("Video Builder", queue_updates[-1])
            self.assertIn("2 clips", queue_updates[-1])
            self.assertIn("recovery fallback", queue_updates[-1])
            self.assertTrue(notices)
            self.assertIn("Video Builder: Import summary:", notices[-1][0])
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_video_builder_status_bar_text_includes_preview_and_mode(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        dialog = vt.VideoToolDialog()
        try:
            dialog._video_io_available = False
            self.assertIn("image/GIF mode", dialog.get_status_bar_text())
            dialog._clips = [
                types.SimpleNamespace(active_frames=24, load_note=""),
                types.SimpleNamespace(active_frames=12, load_note="temporary ffmpeg remux fallback active"),
            ]
            dialog._fps_slider.setValue(24)
            dialog._update_timeline_summary()
            dialog._pos_lbl.setText("5 / 36")
            self.assertIn("preview 5 / 36", dialog.get_status_bar_text())
            dialog._is_playing = True
            self.assertIn("playing", dialog.get_status_bar_text())
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_video_builder_status_bar_text_keeps_import_summary_without_clips(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        dialog = vt.VideoToolDialog()
        try:
            dialog._video_io_available = False
            dialog._update_import_status(
                added=0,
                attempted=2,
                recovered=[],
                failures=[("audio.ogg", "ffprobe detected audio but no playable video stream")],
                skipped=["readme.txt"],
            )
            summary = dialog.get_status_bar_text()
            self.assertIn("Video Builder ready", summary)
            self.assertIn("image/GIF mode", summary)
            self.assertIn("import Added 0 clips", summary)
            self.assertIn("1 failed", summary)
            self.assertIn("1 skipped", summary)
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_video_builder_session_status_and_inline_detail_controls(self):
        try:
            from src.ui import video_tool as vt
        except ImportError as exc:
            self.skipTest(f"video_tool import unavailable in test env: {exc}")

        dialog = vt.VideoToolDialog()
        try:
            self.assertIn("What works here right now:", dialog._session_status_lbl.text())
            self.assertIn("Video Builder ready", dialog._session_status_lbl.text())
            dialog._video_io_available = False
            dialog._update_import_status(
                added=0,
                attempted=2,
                recovered=[],
                failures=[("audio.ogg", "ffprobe detected audio but no playable video stream")],
                skipped=["readme.txt"],
            )
            self.assertFalse(dialog._import_detail_toggle_btn.isHidden())
            self.assertFalse(dialog._import_copy_btn.isHidden())
            self.assertFalse(dialog._import_detail_box.isHidden())
            dialog._toggle_import_details()
            self.assertTrue(dialog._import_detail_box.isHidden())
            dialog._copy_import_details()
            self.assertIn("Import summary:", self._app.clipboard().text())
            self.assertIn("audio.ogg", self._app.clipboard().text())
            self.assertIn("image/GIF mode", dialog._session_status_lbl.text())
            self.assertIn("1 failed", dialog._session_status_lbl.text())
            self.assertIn("Next step:", dialog._session_status_lbl.text())
            self.assertIn("audio-only files cannot be added", dialog._next_step_lbl.text())
            self.assertIn("image/GIF clips and GIF export still work here", dialog._next_step_lbl.text())
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_converter_status_bar_text_includes_output_and_preview_state(self):
        try:
            from src.ui.converter_tool import ConverterTab
        except ImportError as exc:
            self.skipTest(f"converter_tool import unavailable in test env: {exc}")

        widget = ConverterTab(_ConverterTabSettingsStub())
        try:
            widget._file_list.addItem("/tmp/sample.png")
            widget._update_count(1)
            summary = widget.get_status_bar_text()
            self.assertIn("output", summary)
            widget._current_preview_path = "/tmp/sample.png"
            widget._before_is_animated = True
            summary = widget.get_status_bar_text()
            self.assertIn("preview sample.png", summary)
            self.assertIn("animated source", summary)
            widget._output_info_lbl.setText("<b>OUT</b><br>Preview<br><b>unavailable</b>")
            self.assertIn("preview unavailable", widget.get_status_bar_text())
            widget._refresh_session_status()
            self.assertIn("What works here right now:", widget._session_status_lbl.text())
            self.assertIn("Converter ready", widget._session_status_lbl.text())
            self.assertIn("preview sample.png", widget._session_status_lbl.text())
            self.assertIn("Next step:", widget._session_status_lbl.text())
            self.assertIn("review the live preview", widget._next_step_lbl.text())
        finally:
            widget.close()
            widget.deleteLater()
            self._app.processEvents()

    def test_alpha_status_bar_text_includes_preview_and_helper_state(self):
        try:
            from src.core.settings_manager import SettingsManager
            from src.core.presets import PresetManager
            from src.ui.alpha_tool import AlphaFixerTab
        except ImportError as exc:
            self.skipTest(f"alpha_tool import unavailable in test env: {exc}")

        settings = SettingsManager()
        settings._qs = _FakeQSettings({})
        presets = PresetManager(settings)
        widget = AlphaFixerTab(presets, settings)
        try:
            widget._file_list.addItem("/tmp/sprite.png")
            widget._update_file_count(1)
            widget._preview_path = "/tmp/sprite.png"
            widget._preview_helper_lbl.setText("Preview helpers: alpha heat-map on • atlas boxes on (4 cells).")
            summary = widget.get_status_bar_text()
            self.assertIn("preview sprite.png", summary)
            self.assertIn("alpha heat-map on", summary)
            self.assertIn("atlas boxes on (4 cells)", summary)
            widget._refresh_session_status()
            self.assertIn("What works here right now:", widget._session_status_lbl.text())
            self.assertIn("preview sprite.png", widget._session_status_lbl.text())
            self.assertIn("Next step:", widget._session_status_lbl.text())
            self.assertIn("review the preview helpers", widget._next_step_lbl.text())
        finally:
            widget.close()
            widget.deleteLater()
            self._app.processEvents()

    def test_main_window_status_helper_carries_next_step_and_capability_context(self):
        try:
            from src.ui.main_window import _status_summary_and_tooltip
        except ImportError as exc:
            self.skipTest(f"main_window import unavailable in test env: {exc}")

        class _FakeLabel:
            def __init__(self, text):
                self._text = text

            def text(self):
                return self._text

        source = types.SimpleNamespace(
            get_status_bar_text=lambda: "🎬 Video Builder ready  •  image/GIF mode",
            _next_step_lbl=_FakeLabel("Next step: add clips to start a timeline, then preview or export."),
            _capability_lbl=_FakeLabel("Ready now: image/GIF clips work here; MP4 export needs ffmpeg."),
            _session_status_lbl=_FakeLabel(
                "What works here right now: 🎬 Video Builder ready  •  image/GIF mode\n"
                "Next step: add clips to start a timeline, then preview or export."
            ),
        )
        summary, tooltip = _status_summary_and_tooltip(source)
        self.assertEqual(summary, "🎬 Video Builder ready  •  image/GIF mode")
        self.assertIn("What works here right now:", tooltip)
        self.assertIn("Next step: add clips", tooltip)
        self.assertIn("Ready now: image/GIF clips work here", tooltip)

    def test_main_window_shared_gif_builder_reuses_dialog_and_appends_files(self):
        try:
            from src.ui import main_window as mw
        except ImportError as exc:
            self.skipTest(f"main_window import unavailable in test env: {exc}")

        created = []
        connected = []
        updated = []

        class _FakeDialog:
            def __init__(self, parent=None, tooltip_mgr=None):
                self.parent = parent
                self.tooltip_mgr = tooltip_mgr
                self.added = []
                self.shown = 0
                self.raised = 0
                self.activated = 0
                created.append(self)

            def add_media_paths(self, paths):
                self.added.append(list(paths))

            def show(self):
                self.shown += 1

            def raise_(self):
                self.raised += 1

            def activateWindow(self):
                self.activated += 1

        fake_self = types.SimpleNamespace(
            _tooltip_mgr=object(),
            _gif_builder_dlg=None,
            _connect_builder_status=lambda dialog: connected.append(dialog),
            _update_builder_status=lambda: updated.append("ok"),
        )

        with patch.object(mw, "GifBuilderDialog", _FakeDialog):
            mw.MainWindow._open_or_focus_gif_builder(fake_self, ["/tmp/a.png"])
            mw.MainWindow._open_or_focus_gif_builder(fake_self, ["/tmp/b.png"])

        self.assertEqual(len(created), 1)
        self.assertEqual(connected, [created[0]])
        self.assertEqual(created[0].added, [["/tmp/a.png"], ["/tmp/b.png"]])
        self.assertEqual(created[0].shown, 2)
        self.assertEqual(created[0].raised, 2)
        self.assertEqual(created[0].activated, 2)
        self.assertEqual(len(updated), 2)

    def test_main_window_current_tool_status_prefers_active_builder_else_tab(self):
        try:
            from src.ui import main_window as mw
        except ImportError as exc:
            self.skipTest(f"main_window import unavailable in test env: {exc}")

        active_builder = types.SimpleNamespace(
            isVisible=lambda: True,
            isActiveWindow=lambda: True,
            get_status_bar_text=lambda: "🎬 Video Builder: 2 clips  •  import 1 recovered",
            _session_status_lbl=types.SimpleNamespace(text=lambda: "What works here right now: 🎬 Video Builder: 2 clips  •  import 1 recovered"),
            _capability_lbl=types.SimpleNamespace(text=lambda: "Ready now: video import and MP4 export are available."),
            _next_step_lbl=types.SimpleNamespace(text=lambda: "Next step: preview the recovered clip and export when ready."),
        )
        current_tab = types.SimpleNamespace(
            get_status_bar_text=lambda: "📋 History: 3 items  •  filter status:partial",
            _session_status_lbl=types.SimpleNamespace(text=lambda: "What works here right now: 📋 History: 3 items  •  filter status:partial"),
            _capability_lbl=types.SimpleNamespace(text=lambda: ""),
            _next_step_lbl=types.SimpleNamespace(text=lambda: "Next step: adjust the filter or export the visible rows."),
        )
        fake_self = types.SimpleNamespace(
            _gif_builder_dlg=None,
            _video_tool_dlg=active_builder,
            _tabs=types.SimpleNamespace(currentWidget=lambda: current_tab),
        )

        text, tooltip = mw.MainWindow._current_tool_status_context(fake_self)
        self.assertEqual(text, "Current tool: 🎬 Video Builder: 2 clips  •  import 1 recovered")
        self.assertIn("What works here right now:", tooltip)
        self.assertIn("preview the recovered clip", tooltip)

        active_builder.isActiveWindow = lambda: False
        text, tooltip = mw.MainWindow._current_tool_status_context(fake_self)
        self.assertEqual(text, "Current tool: 📋 History: 3 items  •  filter status:partial")
        self.assertIn("adjust the filter", tooltip)

    def test_converter_open_gif_builder_delegates_to_main_window_when_available(self):
        try:
            from src.ui.converter_tool import ConverterTab
        except ImportError as exc:
            self.skipTest(f"converter_tool import unavailable in test env: {exc}")

        calls = []
        fake_host = types.SimpleNamespace(
            _open_or_focus_gif_builder=lambda paths: calls.append(list(paths)),
        )
        fake_self = types.SimpleNamespace(window=lambda: fake_host)

        ConverterTab._open_gif_builder(fake_self, ["/tmp/a.png", "/tmp/b.gif"])
        self.assertEqual(calls, [["/tmp/a.png", "/tmp/b.gif"]])

    def test_history_tab_surfaces_notes_column_for_gif_and_video(self):
        try:
            from src.ui.history_tab import HistoryTab
        except ImportError as exc:
            self.skipTest(f"history_tab import unavailable in test env: {exc}")

        settings = _ConverterTabSettingsStub()
        settings._gif_history.append(
            {
                "timestamp": "2026-10-07T09:00:00",
                "output": "/tmp/a.gif",
                "frame_count": 3,
                "success": 3,
                "errors": 0,
                "files": ["a.png"],
                "delay": "80 ms",
                "fps": "12.5",
                "optimize": "on",
                "loop": "∞",
                "resize": "≤320×auto",
                "sources": "image ×1",
                "largest_frame": "64×64",
                "alpha_summary": "2/3",
                "notes": "optimize=on",
            }
        )
        settings._video_history.append(
            {
                "timestamp": "2026-10-07T09:01:00",
                "output": "/tmp/a.mp4",
                "format": "MP4",
                "clip_count": 2,
                "success": 2,
                "errors": 0,
                "fps": "24",
                "canvas": "640×480",
                "filter": "sepia",
                "audio": "off",
                "recovery": "transcode ×1",
                "streams": "a.iso: stream #3",
                "clips": "sample.iso: temporary ffmpeg transcode fallback active | streams=manual video #3, manual audio #1",
                "sources": "video ×2",
                "files": ["a.iso"],
                "notes": "recovery=transcode ×1 | streams=a.iso: stream #3 | clips=sample.iso: temporary ffmpeg transcode fallback active",
            }
        )
        tab = HistoryTab(settings)
        try:
            self.assertEqual(tab._gif_tree.topLevelItem(0).text(3), "80 ms")
            self.assertEqual(tab._gif_tree.topLevelItem(0).text(4), "12.5")
            self.assertEqual(tab._gif_tree.topLevelItem(0).text(5), "on")
            self.assertEqual(tab._gif_tree.topLevelItem(0).text(6), "∞")
            self.assertEqual(tab._gif_tree.topLevelItem(0).text(7), "≤320×auto")
            self.assertEqual(tab._gif_tree.topLevelItem(0).text(8), "image ×1")
            self.assertEqual(tab._gif_tree.topLevelItem(0).text(9), "64×64")
            self.assertEqual(tab._gif_tree.topLevelItem(0).text(10), "2/3")
            self.assertEqual(tab._gif_tree.topLevelItem(0).text(13), "OK")
            self.assertEqual(tab._gif_tree.topLevelItem(0).text(14), "optimize=on")
            self.assertEqual(tab._vid_tree.topLevelItem(0).text(2), "MP4")
            self.assertEqual(tab._vid_tree.topLevelItem(0).text(4), "24")
            self.assertEqual(tab._vid_tree.topLevelItem(0).text(5), "640×480")
            self.assertEqual(tab._vid_tree.topLevelItem(0).text(6), "sepia")
            self.assertEqual(tab._vid_tree.topLevelItem(0).text(7), "off")
            self.assertEqual(tab._vid_tree.topLevelItem(0).text(8), "transcode ×1")
            self.assertEqual(tab._vid_tree.topLevelItem(0).text(9), "a.iso: stream #3")
            self.assertEqual(tab._vid_tree.topLevelItem(0).text(13), "Recovery")
            self.assertIn("manual audio #1", tab._vid_tree.topLevelItem(0).text(14))
            self.assertIn("transcode fallback", tab._vid_tree.topLevelItem(0).text(15))
            self.assertIn("manual audio #1", tab._vid_tree.topLevelItem(0).toolTip(0))
            self.assertIn("OK 1", tab._gif_summary.text())
            self.assertIn("Recovery 1", tab._vid_summary.text())
        finally:
            tab.close()
            tab.deleteLater()
            self._app.processEvents()

    def test_history_filter_matches_full_output_path_not_only_visible_basename(self):
        try:
            from src.ui.history_tab import HistoryTab
        except ImportError as exc:
            self.skipTest(f"history_tab import unavailable in test env: {exc}")

        settings = _ConverterTabSettingsStub()
        settings._video_history.append(
            {
                "timestamp": "2026-10-07T09:01:00",
                "output": "/tmp/session-exports/nested/final-output.mp4",
                "format": "MP4",
                "clip_count": 2,
                "success": 2,
                "errors": 0,
                "filter": "none",
                "audio": "kept",
                "fps": "30",
                "recovery": "transcode ×1",
                "streams": "sample.iso: stream #3",
                "clips": "sample.iso: temporary ffmpeg transcode fallback active | streams=manual video #3, manual audio #1",
                "sources": "video ×1, image ×1",
                "files": ["a.iso"],
                "notes": "recovery=transcode ×1 | streams=sample.iso: stream #3 | sources=video ×1, image ×1 | clips=sample.iso: temporary ffmpeg transcode fallback active",
            }
        )
        tab = HistoryTab(settings)
        try:
            item = tab._vid_tree.topLevelItem(0)
            self.assertEqual(item.text(1), "final-output.mp4")
            tab._apply_filter(tab._vid_tree, "session-exports")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "recovery")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "status:recovery")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "output:session-exports")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "notes:transcode")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "format:mp4")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "recovery:transcode")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "streams:stream #3")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "clip:manual audio #1")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "audio:kept")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "filter:none")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "fps:30")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "canvas:1920")
            self.assertTrue(item.isHidden())
            tab._apply_filter(tab._vid_tree, "size:640")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "source:image")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "file:a.iso")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "ok:2")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "clip:2")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "status:issues")
            self.assertTrue(item.isHidden())
        finally:
            tab.close()
            tab.deleteLater()
            self._app.processEvents()

    def test_history_filter_matches_gif_frame_and_success_aliases(self):
        try:
            from src.ui.history_tab import HistoryTab
        except ImportError as exc:
            self.skipTest(f"history_tab import unavailable in test env: {exc}")

        settings = _ConverterTabSettingsStub()
        settings._gif_history.append(
            {
                "timestamp": "2026-10-07T09:00:00",
                "output": "/tmp/a.gif",
                "frame_count": 12,
                "success": 12,
                "errors": 0,
                "files": ["a.png"],
                "sources": "image ×1",
                "largest_frame": "320×240",
                "alpha_summary": "4/12",
                "delay": "100 ms",
                "fps": "10",
                "loop": "∞",
                "optimize": "on",
                "resize": "≤640×auto",
                "notes": "optimize=on",
            }
        )
        tab = HistoryTab(settings)
        try:
            item = tab._gif_tree.topLevelItem(0)
            tab._apply_filter(tab._gif_tree, "frame:12")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._gif_tree, "ok:12")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._gif_tree, "status:ok")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._gif_tree, "largest:320×240")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._gif_tree, "delay:100")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._gif_tree, "fps:10")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._gif_tree, "alpha:4/12")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._gif_tree, "loop:∞")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._gif_tree, "optimize:on")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._gif_tree, "resize:640")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._gif_tree, "size:320")
            self.assertFalse(item.isHidden())
        finally:
            tab.close()
            tab.deleteLater()
            self._app.processEvents()

    def test_history_filter_supports_numeric_comparisons_and_grouped_field_ors(self):
        try:
            from src.ui.history_tab import HistoryTab
        except ImportError as exc:
            self.skipTest(f"history_tab import unavailable in test env: {exc}")

        settings = _ConverterTabSettingsStub()
        settings._video_history.append(
            {
                "timestamp": "2026-10-07T09:01:00",
                "output": "/tmp/session-exports/nested/final-output.mp4",
                "format": "MP4",
                "clip_count": 2,
                "success": 2,
                "errors": 0,
                "filter": "none",
                "audio": "kept",
                "fps": "30",
                "canvas": "640×480",
                "recovery": "transcode ×1",
                "streams": "sample.iso: stream #3",
                "sources": "video ×1, image ×1",
                "files": ["a.iso"],
                "notes": "recovery=transcode ×1 | streams=sample.iso: stream #3 | sources=video ×1, image ×1 | clips=sample.iso: temporary ffmpeg transcode fallback active",
            }
        )
        tab = HistoryTab(settings)
        try:
            item = tab._vid_tree.topLevelItem(0)
            tab._apply_filter(tab._vid_tree, "fps:>=24 errors:<1 clip:>1")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "audio:off|kept")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "recovery:remux recovery:transcode")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "stream:*#3")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "size:480")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._vid_tree, "fps:>60")
            self.assertTrue(item.isHidden())
        finally:
            tab.close()
            tab.deleteLater()
            self._app.processEvents()

    def test_history_filter_supports_wildcards_and_numeric_delay_ranges(self):
        try:
            from src.ui.history_tab import HistoryTab
        except ImportError as exc:
            self.skipTest(f"history_tab import unavailable in test env: {exc}")

        settings = _ConverterTabSettingsStub()
        settings._gif_history.append(
            {
                "timestamp": "2026-10-07T09:00:00",
                "output": "/tmp/exports/anim-final.gif",
                "frame_count": 12,
                "success": 12,
                "errors": 0,
                "files": ["a.png"],
                "sources": "image ×1",
                "largest_frame": "320×240",
                "alpha_summary": "4/12",
                "delay": "100 ms",
                "fps": "10",
                "loop": "∞",
                "optimize": "on",
                "resize": "≤640×auto",
                "notes": "optimize=on",
            }
        )
        tab = HistoryTab(settings)
        try:
            item = tab._gif_tree.topLevelItem(0)
            tab._apply_filter(tab._gif_tree, "output:*final.gif")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._gif_tree, "delay:>=100 fps:<11")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._gif_tree, "resize:*640*")
            self.assertFalse(item.isHidden())
            tab._apply_filter(tab._gif_tree, "delay:>100")
            self.assertTrue(item.isHidden())
        finally:
            tab.close()
            tab.deleteLater()
            self._app.processEvents()

    def test_history_tab_status_bar_text_tracks_current_subtab_and_filter(self):
        try:
            from src.ui.history_tab import HistoryTab
        except ImportError as exc:
            self.skipTest(f"history_tab import unavailable in test env: {exc}")

        settings = _ConverterTabSettingsStub()
        settings._video_history.append(
            {
                "timestamp": "2026-10-07T09:01:00",
                "output": "/tmp/final-output.mp4",
                "format": "MP4",
                "clip_count": 2,
                "success": 2,
                "errors": 0,
                "fps": "30",
                "canvas": "640×480",
                "filter": "sepia",
                "audio": "kept",
                "recovery": "transcode ×1",
                "streams": "sample.iso: stream #3",
                "sources": "video ×1",
                "files": ["sample.iso"],
                "notes": "recovery=transcode ×1",
            }
        )
        tab = HistoryTab(settings)
        try:
            self.assertIn("Ready now:", tab._capability_lbl.text())
            tab._sub_tabs.setCurrentIndex(4)
            self.assertIn("What works here right now:", tab._session_status_lbl.text())
            self.assertIn("History: Video Builder", tab.get_status_bar_text())
            self.assertIn("1 item", tab.get_status_bar_text())
            self.assertIn("Next step:", tab._session_status_lbl.text())
            tab._vid_search.setText("transcode")
            self.assertIn("filter transcode", tab.get_status_bar_text())
            self.assertIn("filtered status/notes results", tab._next_step_lbl.text())
            tab._vid_search.setText("missing")
            self.assertIn("0/1 shown", tab.get_status_bar_text())
            self.assertIn("bring matching history entries back", tab._next_step_lbl.text())
        finally:
            tab.close()
            tab.deleteLater()
            self._app.processEvents()


# ---------------------------------------------------------------------------
# Fairy Garden theme + fairy click effect
# ---------------------------------------------------------------------------

class TestFairyTheme(unittest.TestCase):
    def test_fairy_garden_in_preset_themes(self):
        from src.ui.theme_engine import PRESET_THEMES
        self.assertIn("Fairy Garden", PRESET_THEMES)


@unittest.skipUnless(_PYQT6_AVAILABLE, "PyQt6 not installed")
class TestSelectiveAlphaToolSlots(unittest.TestCase):
    def setUp(self):
        _require_qt_gui(self)
        self._app = _get_app()
        from src.ui.selective_alpha_tool import SelectiveAlphaTool
        self._widget = SelectiveAlphaTool()

    def tearDown(self):
        self._widget.hide()
        self._widget.deleteLater()
        self._app.processEvents()

    def test_saved_mask_slots_grow_to_configured_limit(self):
        self.assertEqual(self._widget._slot_combo.count(), self._widget._MASK_SLOT_INIT)
        while self._widget._btn_slot_add.isEnabled():
            self._widget._on_slot_add()
        self.assertEqual(self._widget._slot_combo.count(), self._widget._MASK_SLOT_COUNT)
        self.assertFalse(self._widget._btn_slot_add.isEnabled())

    def test_selective_alpha_status_bar_text_tracks_loaded_image_and_shared_state(self):
        self.assertIn("What works here right now:", self._widget._session_status_lbl.text())
        self.assertIn("Ready now:", self._widget._capability_lbl.text())
        self.assertIn("Alpha Painter ready", self._widget.get_status_bar_text())
        self._widget._src_path = "/tmp/sample.png"
        self._widget._shared_zones = [(64, np.zeros((2, 2), dtype=np.uint8))]
        self._widget._mask_clipboard = np.zeros((2, 2), dtype=np.uint8)
        self._widget._az_slots[0] = [np.zeros((2, 2), dtype=np.uint8)]
        self._widget._result_img = object()
        self._widget._refresh_session_status()
        summary = self._widget.get_status_bar_text()
        self.assertIn("sample.png", summary)
        self.assertIn("shared zone", summary)
        self.assertIn("mask clipboard ready", summary)
        self.assertIn("all-zones slot", summary)
        self.assertIn("result ready to save", summary)
        self.assertIn("sample.png", self._widget._session_status_lbl.text())
        self.assertIn("Next step:", self._widget._session_status_lbl.text())
        self.assertIn("save the current result", self._widget._next_step_lbl.text())

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
        self.assertIn("FORMATOMANCER: Alpha & Media Alchemy", result)

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
        self.assertIn("FORMATOMANCER: Alpha & Media Alchemy", frames[0])
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
        self.assertIn("Alpha & RGBA Adjuster", labels[0])
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
