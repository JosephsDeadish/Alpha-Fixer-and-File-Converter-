import csv
import json
from unittest.mock import patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from src.core.settings_manager import SettingsManager
from src.ui.converter_tool import ConverterTab
from src.ui.history_tab import HistoryTab


class PreviewLoader(QObject):
    ready = pyqtSignal(QImage, QImage, str, str)
    failed = pyqtSignal(str)

    def __init__(self, *args):
        super().__init__()

    def start(self):
        pass

    def stop(self):
        pass


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def settings(app, tmp_path):
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / "settings.ini")):
        manager = SettingsManager()
        yield manager
        manager.sync()
        sip.delete(manager._qs)


@pytest.fixture
def converter(settings):
    widget = ConverterTab(settings)
    with patch("src.ui.converter_tool._ConverterPreviewLoader", PreviewLoader):
        yield widget
        widget._preview_debounce.stop()
        widget._stop_preview_loader()
        widget._compare.clear()
        widget.close()
        sip.delete(widget)


def select_format(converter, fmt):
    index = next(i for i in range(converter._fmt_combo.count())
                 if converter._fmt_combo.itemData(i)[0] == fmt)
    converter._fmt_combo.setCurrentIndex(index)
    converter._on_format_changed(index)
    converter._preview_debounce.stop()


def test_format_change_clears_stale_capability_warning(converter):
    with patch("src.ui.converter_tool.output_format_unavailable_reason", return_value="Codec missing"):
        select_format(converter, "WEBP")
    assert converter._fmt_combo.toolTip() == "Codec missing"
    assert "unavailable" in converter._status_lbl.text()
    with patch("src.ui.converter_tool.output_format_unavailable_reason", return_value=""):
        select_format(converter, "PNG")
    assert converter._fmt_combo.toolTip() == ""
    assert converter._status_lbl.text() == "Ready."
    select_format(converter, "JPEG")
    assert "transparency" in converter._status_lbl.text() or "Transparent" in converter._status_lbl.text()
    select_format(converter, "PNG")
    assert converter._status_lbl.text() == "Ready."


def test_format_change_preserves_busy_and_stopping_status_until_completion(converter):
    converter._btn_run.setEnabled(False)
    converter._btn_run.setText("⠋  Converting…")
    converter._status_lbl.setText("Stopping…")
    select_format(converter, "GIF")
    assert converter._status_lbl.text() == "Stopping…"
    assert converter._btn_run.text() == "⠋  Converting…"
    converter._stop_requested = True
    converter._batch_total = 5
    converter._on_finished(0, 0)
    assert converter._status_lbl.text().startswith("Stopped.")
    assert "Open GIF Builder" in converter._btn_run.text()
    assert "GIF Builder" in converter._btn_run.toolTip()


def test_dds_tooltip_is_updated_even_when_capability_status_is_limited(converter):
    converter._dds_compression_available = False
    converter._dds_variant_combo.setToolTip("stale")
    select_format(converter, "DDS")
    assert "require" in converter._dds_variant_combo.toolTip()
    assert "stale" not in converter._dds_variant_combo.toolTip()


def make_animation(tmp_path, name):
    path = tmp_path / name
    first = Image.new("RGB", (8, 8), "red")
    second = Image.new("RGB", (8, 8), "blue")
    try:
        first.save(path, save_all=True, append_images=[second], duration=100, loop=0)
    finally:
        first.close()
        second.close()
    return str(path)


def test_animation_speed_survives_same_source_refresh_and_matches_movie(converter, tmp_path):
    source = make_animation(tmp_path, "first.gif")
    converter._refresh_preview(source)
    converter._gif_speed_slider.setValue(250)
    assert converter._compare._movie.speed() == 250
    converter._refresh_preview(source)
    assert converter._gif_speed_slider.value() == 250
    assert converter._gif_speed_value_lbl.text() == "250 %"
    assert converter._compare._movie.speed() == 250
    converter._refresh_preview(make_animation(tmp_path, "second.gif"))
    assert converter._gif_speed_slider.value() == 100
    assert converter._gif_speed_value_lbl.text() == "100 %"
    assert converter._compare._movie.speed() == 100


@pytest.mark.parametrize("tab", [0, 1])
@pytest.mark.parametrize("ext,filter_text", [
    ("json", "JSON Files (*.json)"),
    ("csv", "CSV Files (*.csv)"),
    ("txt", "Text Files (*.txt)"),
    ("html", "HTML Files (*.html *.htm)"),
])
def test_history_exports_preserve_filtered_batch_status(settings, tmp_path, tab, ext, filter_text):
    add = settings.add_converter_history if tab == 0 else settings.add_alpha_history
    add({"timestamp": "2026-10-09T11:00:00", "format": "PNG", "preset": "manual",
         "file_count": 5, "success": 2, "errors": 0, "files": ["停止.png"],
         "stopped": True, "not_processed": 3})
    add({"timestamp": "2026-10-09T10:00:00", "format": "PNG", "preset": "manual",
         "file_count": 1, "success": 1, "errors": 0, "files": ["hidden.png"]})
    widget = HistoryTab(settings)
    try:
        widget._sub_tabs.setCurrentIndex(tab)
        tree = widget._conv_tree if tab == 0 else widget._alpha_tree
        HistoryTab._apply_filter(tree, "status:stopped")
        path = tmp_path / f"history.{ext}"
        with patch("src.ui.history_tab.QFileDialog.getSaveFileName",
                   return_value=(str(path), filter_text)):
            with patch("src.ui.history_tab.QMessageBox.information"):
                widget._export_history()
        text = path.read_text(encoding="utf-8")
        assert "Stopped" in text
        assert "Not processed" in text
        assert "停止.png" in text
        assert "hidden.png" not in text
        if ext == "json":
            rows = json.loads(text)
            assert len(rows) == 1
            assert rows[0]["Status"] == "Stopped"
            assert rows[0]["Not processed"] == "3"
        elif ext == "csv":
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            assert len(rows) == 1
            assert rows[0]["Status"] == "Stopped"
            assert rows[0]["Not processed"] == "3"
    finally:
        widget.close()
        sip.delete(widget)


@pytest.mark.parametrize("errors,status", [(0, "OK"), (1, "Issues")])
def test_legacy_completed_history_exports_keep_status_and_zero_remaining(settings, tmp_path, errors, status):
    settings.add_converter_history({
        "timestamp": "2026-10-09T11:00:00", "format": "PNG",
        "file_count": 2, "success": 2 - errors, "errors": errors, "files": ["source.png"],
    })
    widget = HistoryTab(settings)
    try:
        path = tmp_path / "legacy.json"
        with patch("src.ui.history_tab.QFileDialog.getSaveFileName",
                   return_value=(str(path), "JSON Files (*.json)")):
            with patch("src.ui.history_tab.QMessageBox.information"):
                widget._export_history()
        row = json.loads(path.read_text(encoding="utf-8"))[0]
        assert row["Status"] == status
        assert row["Not processed"] == "0"
        assert row["File names"] == "source.png"
    finally:
        widget.close()
        sip.delete(widget)
