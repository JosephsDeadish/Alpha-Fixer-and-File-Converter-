"""Generated-image Converter geometry and keyboard regressions without MainWindow."""
import json
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src.core.settings_manager import SettingsManager
from src.core.worker import ConverterWorker
from src.ui.converter_tool import ConverterTab
from src.ui.preview_pane import _ConverterPreviewLoader
from src.ui.theme_engine import DEFAULT_THEME, LIGHT_THEME, build_stylesheet


class ImmediatePreview(_ConverterPreviewLoader):
    def start(self):
        self.run()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def converter(app):
    directory = Path("build/converter-layout-tests")
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "generated.png"
    with Image.new("RGBA", (80, 40), (30, 80, 150, 120)) as image:
        image.save(source)
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(directory / "settings.ini")):
        settings = SettingsManager()
    with patch("src.ui.converter_tool._ConverterPreviewLoader", ImmediatePreview):
        widget = ConverterTab(settings)
        widget._advanced_toggle.setChecked(True)
        widget._file_list.addItem(str(source))
        widget._file_list.setCurrentRow(0)
        widget._resize_check.setChecked(True)
        widget._width_spin.setValue(60)
        widget._preview_debounce.stop()
        widget._update_converted_preview()
        widget._batch_failure_details = [
            {"source": str(source), "reason": "Generated failure", "output": ""}
        ]
        for button in (widget._btn_retry_failed, widget._btn_keep_failed,
                       widget._btn_skip_failed, widget._btn_export_failures):
            button.setEnabled(True)
        try:
            yield widget
        finally:
            widget.close()
            sip.delete(widget)
            settings.sync()
            sip.delete(settings._qs)
            app.processEvents()
            shutil.rmtree(directory)


def settle(app):
    for _ in range(50):
        app.processEvents()


def visible(widget, control):
    viewport = widget._content_scroll.viewport()
    assert viewport.rect().contains(control.mapTo(viewport, QPoint()))
    assert viewport.rect().contains(control.mapTo(viewport, control.rect().bottomRight()))


def controls(widget):
    return [
        widget._btn_add_files, widget._btn_add_folder, widget._btn_clear,
        widget._recursive_check, widget._out_dir_edit, widget._btn_out_dir,
        widget._suffix_edit, widget._file_list, widget._compare, widget._btn_run,
        widget._fmt_combo, widget._advanced_toggle,
        widget._keep_metadata_check, widget._resize_check,
        widget._width_spin, widget._height_spin, widget._lock_aspect_check,
        widget._btn_retry_failed, widget._btn_keep_failed, widget._btn_skip_failed,
        widget._btn_export_failures,
    ]


@pytest.mark.parametrize("theme", [DEFAULT_THEME, LIGHT_THEME], ids=["dark", "light"])
@pytest.mark.parametrize("pixels", [13, 24, 32])
@pytest.mark.parametrize("size", [(480, 400), (640, 480)])
def test_compact_geometry_focus_resize_preserves_preview(app, converter, theme, pixels, size):
    converter.setStyleSheet(build_stylesheet(theme) + f"\nQWidget {{ font-size: {pixels}px; }}")
    converter.resize(*size)
    converter.show()
    settle(app)
    assert (converter.width(), converter.height()) == size
    assert converter._splitter.orientation() == Qt.Orientation.Vertical
    for scroll in (converter._content_scroll, converter._left_scroll, converter._right_scroll):
        assert scroll.horizontalScrollBar().maximum() == 0
    snapshot = (converter._current_preview_path, converter._width_spin.value(),
                converter._height_spin.value(), converter._preview_request_id,
                converter._source_info_lbl.text(), converter._output_info_lbl.text())
    for control in controls(converter):
        assert control.width() >= control.minimumSizeHint().width()
        assert control.height() >= control.minimumSizeHint().height()
        control.setFocus()
        settle(app)
        assert app.focusWidget() is control
        visible(converter, control)
    converter.resize(2400, 1400)
    settle(app)
    assert converter._splitter.orientation() == Qt.Orientation.Horizontal
    converter.resize(*size)
    settle(app)
    visible(converter, converter._btn_export_failures)
    assert snapshot == (converter._current_preview_path, converter._width_spin.value(),
                        converter._height_spin.value(), converter._preview_request_id,
                        converter._source_info_lbl.text(), converter._output_info_lbl.text())


def test_keyboard_settings_file_actions_and_report_save(app, converter):
    converter.setStyleSheet(build_stylesheet() + "\nQWidget { font-size: 32px; }")
    converter.resize(480, 400)
    converter.show()
    settle(app)
    converter._btn_add_files.setFocus()
    for control in controls(converter):
        if control is converter._btn_run:
            for _ in range(10):
                focused = app.focusWidget()
                if not converter._compare.isAncestorOf(focused):
                    break
                QTest.keyClick(focused, Qt.Key.Key_Tab)
        assert app.focusWidget() is control
        settle(app)
        visible(converter, control)
        QTest.keyClick(control, Qt.Key.Key_Tab)
    converter._btn_export_failures.setFocus()
    QTest.keyClick(converter._btn_export_failures, Qt.Key.Key_Backtab)
    assert app.focusWidget() is converter._btn_skip_failed
    converter._width_spin.setFocus()
    QTest.keyClick(converter._width_spin, Qt.Key.Key_Up)
    assert (converter._width_spin.value(), converter._height_spin.value()) == (61, 30)
    assert converter._preview_debounce.isActive()
    converter._preview_debounce.stop()
    converter._update_converted_preview()
    converter._fmt_combo.setFocus()
    QTest.keyClick(converter._fmt_combo, Qt.Key.Key_Down)
    assert converter._settings.get("last_converter_format") == converter._fmt_combo.currentData()[0]
    converter._suffix_edit.setFocus()
    QTest.keyClicks(converter._suffix_edit, "_converted")
    assert converter._settings.get("output_suffix") == "_converted"
    with patch("src.ui.converter_tool.QFileDialog.getExistingDirectory",
               return_value="build/converter-layout-tests") as folder:
        converter._btn_out_dir.setFocus()
        QTest.keyClick(converter._btn_out_dir, Qt.Key.Key_Space)
        folder.assert_called_once()
    assert converter._settings.get("converter_output_dir") == "build/converter-layout-tests"
    converter._preview_debounce.stop()
    report = Path("build/converter-layout-tests/report.json")
    with patch("src.ui.converter_tool.QFileDialog.getSaveFileName",
               return_value=(str(report), "JSON Report (*.json)")):
        converter._btn_export_failures.setFocus()
        QTest.keyClick(converter._btn_export_failures, Qt.Key.Key_Space)
    assert json.loads(report.read_text())["failures"] == converter._batch_failure_details
    with patch("src.ui.converter_tool.QFileDialog.getOpenFileNames",
               return_value=([], "")) as files:
        converter._btn_add_files.setFocus()
        QTest.keyClick(converter._btn_add_files, Qt.Key.Key_Space)
        files.assert_called_once()
    converter._file_list.setFocus()
    QTest.keyClick(converter._file_list, Qt.Key.Key_Delete)
    assert converter._file_list.count() == 0


def test_compact_dds_options_reveal_without_resetting_preferences(app, converter):
    converter.setStyleSheet(build_stylesheet() + "\nQWidget { font-size: 32px; }")
    converter.resize(480, 400)
    converter.show()
    index = next(i for i in range(converter._fmt_combo.count())
                 if converter._fmt_combo.itemData(i)[0] == "DDS")
    converter._fmt_combo.setCurrentIndex(index)
    converter._preview_debounce.stop()
    settle(app)
    assert converter._dds_variant_combo.isVisible()
    converter._dds_variant_combo.setFocus()
    settle(app)
    visible(converter, converter._dds_variant_combo)
    assert converter._right_scroll.horizontalScrollBar().maximum() == 0
    QTest.keyClick(converter._dds_variant_combo, Qt.Key.Key_Down)
    assert converter._settings.get("last_converter_dds_variant") == converter._dds_variant_combo.currentData()
    saved = converter._dds_variant_combo.currentData()
    converter._preview_debounce.stop()
    converter.resize(2400, 1400)
    settle(app)
    converter.resize(480, 400)
    settle(app)
    visible(converter, converter._dds_variant_combo)
    assert converter._dds_variant_combo.currentData() == saved


def test_progress_preserves_manually_scrolled_viewport(app, converter):
    converter.setStyleSheet(build_stylesheet() + "\nQWidget { font-size: 32px; }")
    converter.resize(480, 400)
    converter.show()
    settle(app)
    converter._file_list.setFocus()
    settle(app)
    visible(converter, converter._file_list)
    bar = converter._content_scroll.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum())
    settle(app)
    position = bar.value()
    assert position == bar.maximum()
    converter._batch_start_time = 0
    with patch("src.ui.converter_tool.time.monotonic", return_value=100):
        for current in (1, 50, 99):
            converter._on_progress(current, 100, "generated.png")
            settle(app)
            assert app.focusWidget() is converter._file_list
            assert bar.value() == position


@pytest.mark.parametrize("key", [Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_disclosure_keys_preserve_generated_preview_and_preferences(app, converter, key):
    converter.resize(480, 400)
    converter.show()
    converter._advanced_toggle.setChecked(False)
    converter._preview_debounce.stop()
    settle(app)
    before = (converter._current_preview_path, converter._preview_request_id,
              converter._width_spin.value(), converter._height_spin.value(),
              converter._compare._raw_after)
    assert "resize 60 × 30 px (aspect locked)" in converter._advanced_status.text()
    assert "retained (not used for PNG)" in converter._advanced_status.text()
    assert not converter._grp_resize.isVisible()
    assert converter._fmt_combo.isVisible()
    assert converter._btn_run.isVisible()
    converter._fmt_combo.setFocus()
    QTest.keyClick(converter._fmt_combo, Qt.Key.Key_Tab)
    assert app.focusWidget() is converter._advanced_toggle
    with patch.object(converter._settings, "set") as write:
        QTest.keyClick(converter._advanced_toggle, key)
        settle(app)
        assert converter._grp_resize.isVisible()
        assert converter._grp_encoding.isVisible()
        assert converter._advanced_toggle.accessibleName().startswith("Hide")
        QTest.keyClick(converter._advanced_toggle, key)
        settle(app)
        assert not converter._grp_resize.isVisible()
        assert converter._advanced_toggle.accessibleName().startswith("Show")
        QTest.keyClick(converter._advanced_toggle, Qt.Key.Key_Tab)
        assert app.focusWidget() is converter._btn_retry_failed
        converter.resize(640, 480)
        settle(app)
        write.assert_not_called()
    assert not converter._preview_debounce.isActive()
    assert before == (converter._current_preview_path, converter._preview_request_id,
                      converter._width_spin.value(), converter._height_spin.value(),
                      converter._compare._raw_after)


def test_saved_options_initially_collapsed_and_format_aware(app, converter):
    settings = converter._settings
    settings.set("last_converter_format", "JPEG")
    settings.set("last_converter_quality", 73)
    settings.set("last_converter_dds_variant", "rgba")
    settings.set("converter_keep_metadata", True)
    with patch.object(settings, "set") as write:
        restored = ConverterTab(settings)
        restored.resize(480, 400)
        restored.show()
        settle(app)
        write.assert_not_called()
    try:
        assert not restored._advanced_toggle.isChecked()
        assert not restored._grp_encoding.isVisible()
        assert not restored._grp_resize.isVisible()
        assert restored._fmt_combo.currentData()[0] == "JPEG"
        summary = restored._advanced_status.text()
        assert "quality 73;" in summary
        assert "preserve supported metadata" in summary
        assert "resize off (original dimensions)" in summary
        assert "retained (not used for JPEG)" in summary
        restored._advanced_toggle.setChecked(True)
        restored._keep_metadata_check.setChecked(False)
        assert "metadata not copied" in restored._advanced_status.text()
        restored._dds_variant_combo.setCurrentIndex(restored._dds_variant_combo.findData("rgb"))
        dds_index = next(i for i in range(restored._fmt_combo.count())
                         if restored._fmt_combo.itemData(i)[0] == "DDS")
        restored._fmt_combo.setCurrentIndex(dds_index)
        restored._preview_debounce.stop()
        assert "quality 73 retained (not used for DDS)" in restored._advanced_status.text()
        restored._keep_metadata_check.setChecked(True)
        assert "metadata enabled, retained (not supported for DDS)" in restored._advanced_status.text()
        assert restored._dds_variant_combo.isVisible()
        restored._dds_variant_combo.setFocus()
        settle(app)
        restored._advanced_toggle.setChecked(False)
        settle(app)
        assert app.focusWidget() is restored._advanced_toggle
        assert restored._dds_variant_combo.currentData() == "rgb"
        assert restored._quality_spin.value() == 73
        assert not restored._dds_variant_combo.isVisible()
        visible(restored, restored._advanced_toggle)
        assert "enabled settings still apply" in restored._advanced_toggle.accessibleDescription()
        gif_index = next(i for i in range(restored._fmt_combo.count())
                         if restored._fmt_combo.itemData(i)[0] == "GIF")
        restored._fmt_combo.setCurrentIndex(gif_index)
        restored._preview_debounce.stop()
        assert "GIF Builder uses its own settings" in restored._advanced_status.text()
    finally:
        restored.close()
        sip.delete(restored)
        app.processEvents()


@pytest.mark.parametrize("resize_enabled", [False, True], ids=["basic", "retained-resize"])
def test_collapsed_run_exports_generated_image_with_retained_options(
        app, converter, resize_enabled):
    class ImmediateWorker(ConverterWorker):
        def start(self):
            self.run()

    converter._resize_check.setChecked(resize_enabled)
    converter._advanced_toggle.setChecked(False)
    converter._out_dir_edit.setText("build/converter-layout-tests/output")
    converter._suffix_edit.setText("_converted")
    converter._preview_debounce.stop()
    converter.resize(480, 400)
    converter.show()
    settle(app)
    with patch("src.ui.converter_tool.ConverterWorker", ImmediateWorker), \
            patch.object(converter, "_offer_delete_originals"), \
            patch.object(converter, "_refresh_preview"):
        converter._btn_run.setFocus()
        settle(app)
        visible(converter, converter._btn_run)
        QTest.keyClick(converter._btn_run, Qt.Key.Key_Space)
    assert converter._btn_run.isEnabled()
    assert converter._progress.value() == 100
    with Image.open("build/converter-layout-tests/output/generated_converted.png") as output:
        assert output.size == ((60, 30) if resize_enabled else (80, 40))
        assert output.getpixel((0, 0))[3] == 120
    assert not converter._advanced_toggle.isChecked()
    assert not converter._grp_resize.isVisible()


@pytest.mark.parametrize("theme", [DEFAULT_THEME, LIGHT_THEME], ids=["dark", "light"])
def test_compact_collapsed_disclosure_wraps_and_is_keyboard_reachable(app, converter, theme):
    converter._advanced_toggle.setChecked(False)
    converter.setStyleSheet(build_stylesheet(theme) + "\nQWidget { font-size: 32px; }")
    converter.resize(480, 400)
    converter.show()
    settle(app)
    for scroll in (converter._content_scroll, converter._left_scroll, converter._right_scroll):
        assert scroll.horizontalScrollBar().maximum() == 0
    for label in (converter._advanced_hint, converter._advanced_status):
        assert label.wordWrap()
        assert label.height() >= label.heightForWidth(label.width())
    for control in (converter._fmt_combo, converter._advanced_toggle, converter._btn_run):
        control.setFocus()
        settle(app)
        assert app.focusWidget() is control
        visible(converter, control)
    assert converter._lbl_fmt.buddy() is converter._fmt_combo
    assert converter._fmt_combo.accessibleName() == "Output format"
