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
        widget._fmt_combo, widget._keep_metadata_check, widget._resize_check,
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
